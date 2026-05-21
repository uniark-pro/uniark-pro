# divergence.py

> Three-segment MACD divergence detector with hierarchical extension and opposite-barrier filtering.

A single-file Python library for detecting **bullish and bearish divergences**
on a MACD histogram using a structural, axiomatic approach:

- **Three-segment structure (S1 + S2 + S3)** — sign-based segmentation of the MACD histogram, then "same / opposite / same" window scanning.
- **Hierarchical extension** — compose `P + S(2k) + S(2k+1)` recursively to detect trend-level divergences (Lv2, Lv3, …).
- **Opposite-barrier filtering** — automatically discard structures whose interior is broken by an opposite Lv≥2 divergence (the two halves belong to different regimes and shouldn't be merged).
- **Provisional flagging** — final segments that haven't "closed" yet are marked so you don't accidentally introduce lookahead bias.

---

## Install

It's a single file with one dependency (`numpy`). Drop `divergence.py` into your project, or:

```bash
# requirements:
pip install numpy pandas
```

---

## Quickstart

```python
import pandas as pd
from divergence import find_three_segment_divergences

# 1) Bring your own OHLCV data
df = pd.read_csv('btc_daily.csv', parse_dates=['date'], index_col='date')

# 2) Compute MACD (12/26/9)
ema_fast = df['close'].ewm(span=12, adjust=False).mean()
ema_slow = df['close'].ewm(span=26, adjust=False).mean()
df['hist'] = (ema_fast - ema_slow) - (ema_fast - ema_slow).ewm(span=9, adjust=False).mean()

# 3) Detect
divs = find_three_segment_divergences(df['hist'], df['low'], df['high'])

# 4) Inspect
for d in divs:
    print(d['kind'], 'Lv', d['level'],
          'ratio=', round(d['ratio'], 2),
          'at', df.index[d['s3_end']])
```

Output:

```
bullish Lv 1 ratio= 0.34 at 2022-07-18
bearish Lv 1 ratio= 0.41 at 2023-09-15
...
```

---

## API

```python
find_three_segment_divergences(
    hist_series,           # pd.Series  MACD histogram (positive green, negative red)
    low_series,            # pd.Series  candle lows
    high_series,           # pd.Series  candle highs
    min_bars=0,            # min bars per segment (noise filter)
    ratio_threshold=0.5,   # area-ratio threshold for divergence trigger
    max_level=1,           # 1 = base 3-seg; 2 = + Lv2 (P+S4+S5); None = exhaustive
    block_by_opposite=True # apply opposite-barrier rule
) -> list[dict]
```

Each result dict contains:

| Field | What it is |
|-------|------------|
| `kind` | `'bullish'` / `'bearish'` |
| `level` | Triggering level: 1 = base 3-segment, 2+ = hierarchical extension |
| `s1_start`, `s1_end` | Left-side body P (positional indices, **not timestamps**) |
| `s3_start`, `s3_end` | Final same-sign segment S_last (the trigger point is `s3_end`) |
| `s1_area`, `s3_area` | Areas (sum of `|hist|`) |
| `s1_bars`, `s2_bars`, `s3_bars` | Bar counts |
| `ratio` | `s3_area / s1_area` — smaller = stronger divergence |
| `provisional` | `True` = final segment not yet closed; treat with caution |
| `same_terminal_l1` | `True` = multi-scale convergence at this terminal |

> **Important**: returned `*_start` / `*_end` are **integer positions**, not DatetimeIndex labels. Recover timestamps with `df.index[idx]`.

---

## At a glance

```
   Bullish divergence                Bearish divergence
   ───────────────────               ───────────────────
                                                     ▼
        S1            S3                S1          S3
        ▓▓            ▒                  ░░          ░
        ▓▓    S2      ▒                  ░░    S2    ░
   ─────▓▓────░──────▒───              ──░░────▓────░───
                                                       
        ▲                                              
   ratio = S3.area / S1.area  <  0.5                  
   price makes a new low / high                        
```

---

## Common patterns

```python
# Default: base 3-segment + barrier filtering
divs = find_three_segment_divergences(df['hist'], df['low'], df['high'])

# Multi-scale analysis (trend-level divergences)
divs = find_three_segment_divergences(df['hist'], df['low'], df['high'], max_level=3)

# For backtesting: filter out tentative signals
confirmed = [d for d in divs if not d['provisional']]

# For debugging: see all raw candidates
all_cands = find_three_segment_divergences(
    df['hist'], df['low'], df['high'],
    max_level=None, block_by_opposite=False,
)
```

---

## Tuning quick-reference

| Setting | Default | When to change |
|---------|---------|----------------|
| `ratio_threshold` | 0.5 | Stricter: 0.3~0.4; Looser: 0.6~0.7 |
| `max_level` | 1 | Daily: 2~3; Weekly: 2~None; Intraday: keep 1 |
| `min_bars` | 0 | Daily: 1~3; Hourly: 3~5; Sub-hourly: 5+ |
| `block_by_opposite` | True | Keep True except for debugging |

---

## Documentation

- **Full usage tutorial**: see [`TUTORIAL.md`](TUTORIAL.md) for input contracts, parameter tuning rationale, visualization recipes, and 5 common pitfalls.
- **Algorithm specification**: read the module-level docstring in `divergence.py` itself — it's the authoritative reference for the axiomatic structure, hierarchical extension semantics, and the opposite-barrier rule.

---

## License

MIT (or whatever the project chooses).
