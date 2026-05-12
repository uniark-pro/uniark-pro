# `divergence.py` — Library Usage Guide

> This guide is for using `divergence.py` as a standalone library. The module-level
> docstring remains the authoritative specification; this guide focuses on
> **how to call it, how to interpret results, how to tune parameters, and what
> pitfalls to avoid.**

---

## 0. What it does

`divergence.py` detects **three-segment divergence structures** on a MACD
histogram (`hist`) — the classic "S1 (same-sign) + S2 (opposite-sign) + S3
(same-sign)" pattern — plus hierarchical recursive extensions (P+S4+S5,
P+S6+S7, …) and opposite-barrier filtering.

The module exposes a single public function:

```python
find_three_segment_divergences(
    hist_series,           # pd.Series  MACD histogram
    low_series,            # pd.Series  candle lows
    high_series,           # pd.Series  candle highs
    min_bars=0,            # minimum bars per segment (noise filter)
    ratio_threshold=0.5,   # area-ratio threshold
    max_level=1,           # extension depth: 1=base 3-seg, 2=P+S4+S5, None=exhaustive
    block_by_opposite=True # apply the opposite-barrier rule?
)
```

It returns a `list[dict]`, one record per detected divergence.

---

## 1. Quickstart: get your first result in 10 lines

```python
import pandas as pd
from divergence import find_three_segment_divergences

# 1) Bring your own OHLCV data. Any source works — just need columns
#    named open/high/low/close.
df = pd.read_csv('btc_daily.csv', parse_dates=['date'], index_col='date')

# 2) Compute MACD yourself (standard 12/26/9 parameters)
ema_fast = df['close'].ewm(span=12, adjust=False).mean()
ema_slow = df['close'].ewm(span=26, adjust=False).mean()
macd     = ema_fast - ema_slow
signal   = macd.ewm(span=9, adjust=False).mean()
df['hist'] = macd - signal

# 3) Call the detector
divs = find_three_segment_divergences(df['hist'], df['low'], df['high'])

# 4) Inspect the results
for d in divs:
    print(d['kind'], 'Lv', d['level'],
          'ratio=', round(d['ratio'], 2),
          'at', df.index[d['s3_start']], '→', df.index[d['s3_end']])
```

Output looks like:

```
bullish Lv 1 ratio= 0.34 at 2022-06-10 → 2022-07-18
bearish Lv 1 ratio= 0.41 at 2023-08-02 → 2023-09-15
...
```

It works. Now let's break down what's going on.

---

## 2. Input contract (easy to overlook, but critical)

### 2.1 All three Series must share the same index and length

`hist_series`, `low_series`, and `high_series` are accessed **positionally**
(via `.iloc`) inside the function. The three Series you pass in must be
corresponding rows of the same DataFrame. The safest pattern is always:

```python
divs = find_three_segment_divergences(df['hist'], df['low'], df['high'])
```

Don't try to stitch together three Series from different sources. If they
have different lengths or misaligned indices, the result will be **silently
wrong** — the function won't raise an error.

### 2.2 Returned indices are **positional integers**, not DatetimeIndex labels

The fields `s1_start`, `s1_end`, `s3_start`, `s3_end` in the returned dicts
are **integer offsets** (0-based positions), **not** timestamps. To recover
the time, use `df.index[idx]`:

```python
for d in divs:
    t_start = df.index[d['s3_start']]
    t_end   = df.index[d['s3_end']]
    print(f"Lv{d['level']} {d['kind']}: {t_start} ~ {t_end}")
```

**Corollary**: the DataFrame you use to interpret results must be the same
one whose `hist` column you passed to the detector. If you detected on
`df['hist']` but later try to interpret with `df.iloc[100:]['hist']`,
every index will be off by 100.

### 2.3 NaN handling

The function tolerates NaN, with one caveat: **NaN terminates the current
segment** — if a same-sign run of `hist` values has a NaN in the middle,
the two halves are cut into two segments rather than treated as one. In
the warmup period (the first ~26 bars where MACD isn't stable) this isn't
an issue because the whole stretch is NaN; but scattered NaNs in the middle
of valid data will fragment your segments.

The safest pattern is to dropna before calling:

```python
df = df.dropna(subset=['hist']).reset_index(drop=False)
# Note: after reset_index, positions become 0..N-1 fresh; original
# timestamps are preserved as a column.
```

Or simply:

```python
df = df.iloc[26:].copy()   # skip the warm-up bars where MACD isn't stable yet
```

### 2.4 You need enough data

Each base three-segment divergence needs 3 segments. A Lv2 extension needs
5 segments. Since segments come from `hist` sign-flips, **bars needed ≈
segment count × average segment length**. On daily charts, a divergence
spans anywhere from 2 months to over a year. Plan for at least 100–200 bars
to see any Lv1 signals, and 300+ for Lv2.

---

## 3. Interpreting the results

### 3.1 Field reference

| Field | Type | Meaning |
|-------|------|---------|
| `kind` | `'bullish'` / `'bearish'` | Bullish (potential reversal up) / Bearish (potential reversal down) |
| `level` | int | Triggering level. 1 = base S1+S2+S3; 2 = P+S4+S5; 3 = P+S6+S7; … |
| `s1_start` | int | **Start position of the left-side body P.** For Lv1 = start of S1; for Lv2 = start of S1 (P spans S1+S2+S3) |
| `s1_end` | int | End position of left-side body P |
| `s3_start` | int | **Start of the final same-sign segment S_last.** For Lv1 = S3; for Lv2 = S5 |
| `s3_end` | int | End of the final same-sign segment — this is the **trigger point** |
| `s1_area` | float | Total area of the left-side body (same-sign members only; opposite segments don't count) |
| `s3_area` | float | Area of the final same-sign segment |
| `s1_bars` | int | Span of the left-side body in bars (includes intermediate opposite segments) |
| `s2_bars` | int | Bars in the opposite segment immediately preceding S_last |
| `s3_bars` | int | Bars in the final same-sign segment |
| `ratio` | float | `s3_area / s1_area` — the "momentum decay ratio" |
| `provisional` | bool | True = the final segment hasn't "closed" yet (see §3.2) |
| `same_terminal_l1` | bool | True = a Lv1 divergence also holds independently at this Lv≥2's terminal (see §3.3) |

`ratio` — smaller means stronger divergence:
- `ratio = 0.3`: S_last has only 30% of the prior body's momentum
- `ratio = 0.5`: the default threshold ceiling (anything weaker won't be detected)
- `ratio = 0.1`: a very strong exhaustion signal

`level` — at what scale exhaustion is happening:
- Lv1 = local three-segment divergence (short-term momentum exhaustion)
- Lv2 = exhaustion across a structure that spans one intermediate opposite swing (medium-term)
- Lv3+ = exhaustion across multiple intermediate retracements (long-term trend exhaustion)

### 3.2 `provisional`: tentative vs. confirmed signals

When the right end of S_last equals the last index of the hist series,
`provisional=True`. The semantics:

> This segment hasn't "closed" yet — if future bars are still the same sign,
> S_last will extend; only after a sign-flip does the segment finalize.
> The current `ratio` is a snapshot, not a verdict.

Practical implication:
- `provisional=False`: confirmed historical signal (use for backtesting, statistics)
- `provisional=True`: still forming; both the trigger time and the magnitude may change (use for live monitoring)

⚠️ A common bug: if you don't filter `provisional` in a backtest, you'll
treat "tentative signals on the last bar" as confirmed signals, creating
a phantom lookahead bias. Recommended:

```python
confirmed   = [d for d in divs if not d['provisional']]
provisional = [d for d in divs if d['provisional']]
```

### 3.3 `same_terminal_l1`: multi-scale convergence

This can only be True when `level >= 2`. Semantics:

> At the terminal position (s3_start, s3_end) of this Lvk divergence, the
> area ratio of S_last against just its immediate prior same-sign segment
> *also* passes the Lv1 threshold independently.

These two conditions are not implied by each other — Lvk being small
(`S_last / sum(all prior same-sign segments)`) doesn't mean Lv1 is small
(`S_last / immediately-prior segment`). When both fire simultaneously,
it means **"momentum exhaustion is happening at multiple scales at once"**.
Empirically a stronger signal.

UIs typically render this as a "double arrow" (vs. a single arrow). If you
only care about binary signals, ignore this field. If you want to grade
signal strength, fold it in.

---

## 4. Common call patterns

### 4.1 Defaults (backward-compatible)

```python
divs = find_three_segment_divergences(df['hist'], df['low'], df['high'])
# Equivalent to:
# min_bars=0, ratio_threshold=0.5, max_level=1, block_by_opposite=True
```

Detects only base three-segment divergences with barrier filtering. This is
the daily-analysis workhorse.

### 4.2 Enable hierarchical extension (find trend-level divergences)

```python
divs = find_three_segment_divergences(
    df['hist'], df['low'], df['high'],
    max_level=2,   # detect Lv1 and Lv2
)
```

Or be exhaustive:

```python
divs = find_three_segment_divergences(
    df['hist'], df['low'], df['high'],
    max_level=None,  # let the algorithm decide
)
```

`max_level=None` on long sequences (>500 bars) will explore many high-order
combinations; most get filtered out by the barrier rule, leaving mostly
Lv2~Lv3 survivors. A conservative cap like `max_level=3` is usually enough.

### 4.3 Debug mode: see all raw candidates

```python
all_candidates = find_three_segment_divergences(
    df['hist'], df['low'], df['high'],
    max_level=None,
    block_by_opposite=False,   # ← turn off barrier filtering
)
```

This returns **every** candidate that passes the area-ratio and price-
extremes tests, including those that would be barred by an opposite D'.
Useful for:
- Verifying the barrier rule behaves as expected
- Reproducing pre-barrier behavior (`block_by_opposite` is a later addition)
- Building a "candidate pool" for custom downstream filtering

### 4.4 Noise filter: `min_bars`

```python
divs = find_three_segment_divergences(
    df['hist'], df['low'], df['high'],
    min_bars=3,    # segments shorter than 3 bars get merged into adjacent opposite segments
)
```

`min_bars` only affects **level-1** minimum-segment filtering (high-level P
is composite, its bar count is necessarily large, so further filtering
is moot).

Rules of thumb:
- `min_bars=0` (default): no filtering. Most sensitive, most noise.
- `min_bars=2~3`: standard for daily, 3-day, weekly.
- `min_bars=5+`: noisy intraday timeframes.

Note: when `min_bars=0`, the merge step is a no-op; when nonzero, it
actively rewrites the segment sequence, and some patterns that "look like"
three segments may get merged out of existence. This is algorithm semantics,
not a bug.

---

## 5. A minimal visualization recipe

If you want to plot results yourself (without the project's `plot_helpers`),
here's a minimal implementation:

```python
import matplotlib.pyplot as plt

def plot_divergences(df, divs):
    fig, (ax_price, ax_macd) = plt.subplots(
        2, 1, figsize=(14, 8), sharex=True,
        gridspec_kw={'height_ratios': [3, 1]}
    )

    # Top: closing price
    ax_price.plot(df.index, df['close'], color='black', linewidth=1)
    ax_price.set_title('Price')

    # Bottom: MACD histogram
    colors = ['#cc3333' if h < 0 else '#33aa33' for h in df['hist']]
    ax_macd.bar(df.index, df['hist'], color=colors, width=0.8)
    ax_macd.axhline(0, color='gray', linewidth=0.5)
    ax_macd.set_title('MACD Histogram')

    # Annotate divergences
    for d in divs:
        x_mid_idx = (d['s3_start'] + d['s3_end']) // 2
        x_mid = df.index[x_mid_idx]
        ratio_pct = d['ratio'] * 100

        if d['kind'] == 'bullish':
            color = '#1e90ff' if d['provisional'] else '#ff3344'
            y = df['hist'].iloc[d['s3_start']:d['s3_end']+1].min()
            marker = '^'
            text_y_offset = -abs(y) * 0.5
        else:
            color = '#1e90ff' if d['provisional'] else '#22aa44'
            y = df['hist'].iloc[d['s3_start']:d['s3_end']+1].max()
            marker = 'v'
            text_y_offset = abs(y) * 0.5

        # Double arrow for multi-scale convergence
        if d.get('same_terminal_l1', False):
            ax_macd.scatter([x_mid, x_mid], [y, y], marker=marker,
                            s=[100, 60], color=color, zorder=5)
        else:
            ax_macd.scatter([x_mid], [y], marker=marker,
                            s=80, color=color, zorder=5)

        suffix = ' ?' if d['provisional'] else ''
        label = f"L{d['level']}\n{ratio_pct:.0f}%{suffix}"
        ax_macd.annotate(label, xy=(x_mid, y),
                         xytext=(0, text_y_offset),
                         textcoords='offset points',
                         ha='center', va='center',
                         fontsize=8, color=color)

    plt.tight_layout()
    return fig

fig = plot_divergences(df, divs)
fig.savefig('divs.png', dpi=120)
```

Key details:
- Arrow x-coordinate is `(s3_start + s3_end) / 2`, the midpoint of S_last
- Arrow y-coordinate sits on the hist extreme bar (deepest red for bullish, tallest green for bearish)
- `provisional=True` switches the text color to blue and appends `?`
- `same_terminal_l1=True` draws a double arrow (two markers slightly offset)

If you prefer to use the project's built-in `plot_helpers.annotate_divergences`,
attach it to a mplfinance or custom matplotlib axes — its signature is
`(macd_ax, df, divergences)`, with the constraint that `df` must have a
`'hist'` column and an index aligned with the one used during detection.

---

## 6. Parameter tuning recommendations

### 6.1 `ratio_threshold` (default 0.5)

Controls "how weak a decay still counts as divergence":
- `0.5` is the empirical default, balancing sensitivity and false positives.
- `0.3~0.4`: stricter. Catches only clear exhaustion, low false-positive rate, more missed signals.
- `0.6~0.7`: looser. Includes mild decay, higher false-positive rate.

**Don't go above 0.7** — semantically that's no longer "exhaustion".

### 6.2 `max_level`

| Timeframe | Recommended | Why |
|-----------|-------------|-----|
| Hourly, intraday | `1` | Noisy data; high-order divergences are usually coincidence |
| Daily | `2` or `3` | The sweet spot for standard multi-scale analysis |
| Weekly, 3-day | `2` ~ `None` | Less data, less noise — can afford to be liberal |

### 6.3 `min_bars`

Empirically inverse to timeframe — shorter timeframes have more noise and
need higher `min_bars`:

| Timeframe | Recommended `min_bars` |
|-----------|------------------------|
| Weekly | 0 ~ 1 |
| Daily, 3-day | 1 ~ 3 |
| 4h, 1h | 3 ~ 5 |
| 30m, 15m | 5+ |

### 6.4 `block_by_opposite`

In practice **always keep this True**. Setting it to `False` bypasses the
core semantic filter: under the axiom "**a triggered opposite divergence
marks the starting point of the next same-direction structure**", a new
structure is invalidated if its formation spans an already-triggered
opposite divergence (the application layer additionally requires the
barrier to be at least L≥2, so that adjacent L1+L1 reversal signals don't
mutually cancel).

Legitimate uses of `False`:
- Debugging the algorithm itself
- Generating a "candidate pool" for custom downstream filtering
- Reproducing the behavior of versions before the barrier rule existed

---

## 7. Common pitfalls

### Pitfall 1: index misalignment

```python
# ❌ WRONG
df_recent = df.iloc[-200:]   # take the last 200 bars
divs = find_three_segment_divergences(df['hist'], df['low'], df['high'])
                                     # ↑ but detected on the full dataset!
for d in divs:
    print(df_recent.index[d['s3_start']])   # 💥 off-by-N indexing
```

```python
# ✅ CORRECT (either use the full data everywhere, or the slice everywhere)
df_recent = df.iloc[-200:].reset_index().rename(columns={'index': 'date'})
# Or directly:
divs = find_three_segment_divergences(
    df_recent['hist'], df_recent['low'], df_recent['high']
)
```

### Pitfall 2: unfiltered `provisional` in backtests

```python
# ❌ implicit lookahead bias
total_signals = len(divs)
```

```python
# ✅ for backtests / statistics, only use confirmed signals
confirmed = [d for d in divs if not d['provisional']]
```

### Pitfall 3: treating `s3_end` as the "reversal time"

`s3_end` is the bar where the final segment was last observed — it's the
**detection moment**, not the "reversal starting point". The actual price
reversal may occur at `s3_end + 1, +2, +N` (or never — divergences fail too).

Downstream trade-signal logic should:
- Enter on some trigger after `s3_end` (e.g. breaking the prior low/high)
- Don't take the close at `s3_end` as the entry price

### Pitfall 4: MACD parameters don't match what you expect

This module makes no assumptions about MACD parameters — it computes
whatever `hist` you give it. If you use non-standard parameters like
(5, 35, 5) instead of (12, 26, 9), the module still works, but the
empirical `ratio_threshold=0.5` default may no longer apply (you'll need
to recalibrate it for your parameter combination).

### Pitfall 5: weekends / holidays causing "false sign-flips"

24/7 crypto markets don't have this issue. But on equity charts, holiday
gaps sometimes cut what would otherwise be a continuous same-sign hist
into two segments. If you see divergence judgments around holidays that
contradict your intuition, inspect the raw hist sequence — `min_bars`
will usually merge the artifact away.

---

## 8. Advanced: calling `find_hist_segments` directly

If you want to bypass the divergence logic entirely and just use the
"sign-based segmentation" utility:

```python
from divergence import find_hist_segments

segs = find_hist_segments(df['hist'])
# Returns list[dict], each:
#   { 'sign': 'pos'|'neg', 'start': int, 'end': int,
#     'area': float, 'bars': int }
```

Useful for:
- Building your own multi-segment comparison logic
- Computing the "segment length distribution" of a hist series to choose `min_bars` empirically
- Visualizing the segments themselves (color-coded blocks)

`find_hist_segments` is pure-functional and side-effect-free — safe to use
as a standalone utility.

---

## Appendix: complete example

Tying everything together:

```python
import pandas as pd
import matplotlib.pyplot as plt
from divergence import find_three_segment_divergences

# === 1. Load data ===
df = pd.read_csv('btc_daily.csv', parse_dates=['date'], index_col='date')

# === 2. Compute MACD ===
def add_macd(df, fast=12, slow=26, signal=9):
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    sig  = macd.ewm(span=signal, adjust=False).mean()
    df['hist'] = macd - sig
    return df

df = add_macd(df).dropna(subset=['hist'])

# === 3. Detect divergences ===
divs = find_three_segment_divergences(
    df['hist'], df['low'], df['high'],
    min_bars=2,
    ratio_threshold=0.5,
    max_level=2,
    block_by_opposite=True,
)

# === 4. Split confirmed / provisional ===
confirmed   = [d for d in divs if not d['provisional']]
provisional = [d for d in divs if d['provisional']]

print(f"{len(confirmed)} confirmed signals, {len(provisional)} provisional")

# === 5. Print each signal ===
for d in divs:
    t_start = df.index[d['s3_start']]
    t_end   = df.index[d['s3_end']]
    tag     = '?' if d['provisional'] else ''
    co      = '+L1' if d.get('same_terminal_l1') else ''
    print(f"[{d['kind']:8s} Lv{d['level']}{tag}] "
          f"{t_start.date()} ~ {t_end.date()} "
          f"ratio={d['ratio']*100:.0f}% {co}")

# === 6. Visualize (use plot_helpers or roll your own) ===
# See §5
```

---

## Quick reference

| Task | Call |
|------|------|
| Daily use: find base 3-segment divergences | all defaults |
| Multi-scale analysis: find trend-level divergences | `max_level=2` or `3` |
| Noisy hourly data | `min_bars=3`, `max_level=1` |
| Live monitoring | all defaults; handle `provisional` separately |
| Backtest: avoid lookahead bias | filter out `provisional=True` |
| Debugging / reproducing old behavior | `block_by_opposite=False` |
| Signal strength scoring | combine `ratio`, `level`, `same_terminal_l1` |

---

**A final word**: this is an *onboarding* guide. The module-level docstring
and each internal function's docstring are the **authoritative specification**.
If the two disagree, the code wins.
