"""
K-line plotting — unified entry point for multiple symbols and intervals.
========================================================================
All "symbol" and "interval" specific differences are confined to
SYMBOL_CONFIG and INTERVAL_CONFIG; the plotting core stays single-
implementation.

Symbol whitelist semantics
--------------------------
SYMBOL_CONFIG is no longer an "allowed symbols whitelist" — it's only
an "explicit override config". Symbols added via settings.py / the
settings UI that don't have an explicit entry here will be auto-derived
through _resolve_symbol_config() (with the USDT suffix stripped to
form the short name). Trading pairs that don't exist on Binance will
be reported by Binance's API itself.

CLI output (consumed by main.py):
  - "Image saved: <path>"   ← image path
  - "BARS=<N>"              ← actual K-line count rendered

Data-fetch strategy (precise, on demand):
  Compute the actual fetch window from the requested [start_str, end_str]:
      fetch_start = start_str - LOOKBACK_BARS × interval_duration
      fetch_end   = end_str  (None → fetch up to current time)
  LOOKBACK_BARS=200 ensures MA99 has stabilized by the time we reach
  start_str (99×2+ buffer); the same buffer is more than enough for
  MACD too (≈ 5×slow ≈ 130 bars to converge).

  Within the default range presets in the main UI (weekly 3 years /
  3day 1 year / daily 4 months), the total bars fetched is always
  under 1000, corresponding to a single Binance HTTP call (~0.3 to
  1 second).
"""
import sys
import os
import datetime as _dt
import matplotlib
matplotlib.use('Agg')
matplotlib.rcParams['font.sans-serif'] = ['DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False

from data import get_klines
from indicator import add_indicators
from divergence import find_three_segment_divergences
from plot_helpers import annotate_divergences, print_divergences
from navigation import INTERVAL_MINUTES
import mplfinance as mpf
import matplotlib.pyplot as plt
import pandas as pd


# ── Symbol explicit config (optional) ─────────────────────────────────
# This is only an "explicit override table" for the auto-derivation
# below. Symbols not listed here go through _resolve_symbol_config and
# are auto-derived; adding a new symbol typically only requires
# adding it via settings.py / the settings UI, with no need to touch
# this file. Add an entry here only when a symbol needs a special
# short / display name.
SYMBOL_CONFIG = {
    'BTCUSDT': {'short': 'BTC', 'cn_name': 'BTC'},
    'ETHUSDT': {'short': 'ETH', 'cn_name': 'ETH'},
    'SOLUSDT': {'short': 'SOL', 'cn_name': 'SOL'},
    'BNBUSDT': {'short': 'BNB', 'cn_name': 'BNB'},
    'FILUSDT': {'short': 'FIL', 'cn_name': 'FIL'},
}


# Known quote-currency suffixes. Matched in descending order of length
# (USDT before USD).
_QUOTE_SUFFIXES = ('USDT', 'USDC', 'BUSD', 'TUSD', 'FDUSD', 'DAI',
                   'BTC', 'ETH', 'BNB', 'USD', 'EUR')


def _resolve_symbol_config(symbol):
    """
    Return {'short': ..., 'cn_name': ...}.
    Prefer the explicit entry in SYMBOL_CONFIG; if absent, derive from
    the symbol (strip a known quote-currency suffix to get short;
    cn_name same as short).
    """
    if symbol in SYMBOL_CONFIG:
        return SYMBOL_CONFIG[symbol]
    short = symbol
    for q in _QUOTE_SUFFIXES:
        if symbol.endswith(q) and len(symbol) > len(q):
            short = symbol[:-len(q)]
            break
    return {'short': short, 'cn_name': short}


# ── Interval config ───────────────────────────────────────────────────
INTERVAL_CONFIG = {
    '15m':    {'label': '15m',    'file_prefix': '15m',    'cn_name': '15m',     'min_bars': 0, 'max_level': None},
    '30m':    {'label': '30m',    'file_prefix': '30m',    'cn_name': '30m',     'min_bars': 0, 'max_level': None},
    '1h':     {'label': '1h',     'file_prefix': '1h',     'cn_name': '1h',      'min_bars': 0, 'max_level': None},
    '4h':     {'label': '4h',     'file_prefix': '4h',     'cn_name': '4h',      'min_bars': 0, 'max_level': None},
    'daily':  {'label': 'Daily',  'file_prefix': 'daily',  'cn_name': 'Daily',   'min_bars': 0, 'max_level': None},
    '3day':   {'label': '3-Day',  'file_prefix': '3day',   'cn_name': '3-Day',   'min_bars': 0, 'max_level': None},
    'weekly': {'label': 'Weekly', 'file_prefix': 'weekly', 'cn_name': 'Weekly',  'min_bars': 0, 'max_level': None},
}


MA_PERIODS = (7, 25, 99)
MA_COLORS  = ('#ff9900', '#cc44ff', '#00aaff')

TOO_MUCH_DATA_THRESHOLD = 600
LOOKBACK_BARS = 200
DATA_FLOOR = pd.Timestamp('2017-07-01')


def calc_ma(series, period):
    return series.rolling(window=period).mean()


def add_ma(df):
    for p in MA_PERIODS:
        df[f'ma{p}'] = calc_ma(df['close'], p)
    return df


def get_macd_colors(hist):
    return ['g' if v >= 0 else 'r' for v in hist]


def _compute_fetch_start(interval, request_start_str):
    """Push the request start back by LOOKBACK_BARS to obtain the
    actual fetch start."""
    request_start = pd.Timestamp(request_start_str)
    minutes = INTERVAL_MINUTES[interval]
    lookback = _dt.timedelta(minutes=minutes * LOOKBACK_BARS)
    fetch_start = request_start - lookback
    if fetch_start < DATA_FLOOR:
        fetch_start = DATA_FLOOR
    return fetch_start.strftime('%d %b, %Y %H:%M:%S')


def render_chart(symbol, interval, start_str=None, end_str=None):
    """Render a K-line + MACD + divergence-annotation chart for the
    specified symbol, interval, and time range."""
    if interval not in INTERVAL_CONFIG:
        raise ValueError(
            f"Unknown interval: {interval!r}. "
            f"Valid: {list(INTERVAL_CONFIG.keys())}"
        )

    sym_cfg = _resolve_symbol_config(symbol)
    iv_cfg  = INTERVAL_CONFIG[interval]

    if start_str:
        fetch_start = _compute_fetch_start(interval, start_str)
    else:
        fetch_start = '17 Aug, 2017 00:00:00'

    # Pass end_str to the API so Binance truncates the right end
    # directly, avoiding an unnecessary "fetch up to now". When
    # end_str=None (caller did not specify), let the remote fetch
    # all the way to current time.
    df = get_klines(symbol, interval,
                    start_str=fetch_start,
                    end_str=end_str if end_str else None)
    df = add_indicators(df)
    df = add_ma(df)
    # Keep the in-memory filter to handle floating-point boundaries /
    # timezone fold cases (cost is negligible).
    if start_str:
        df = df[df.index >= pd.Timestamp(start_str)]
    if end_str:
        df = df[df.index <= pd.Timestamp(end_str)]

    if len(df) == 0:
        raise ValueError(
            f"No data in range for {symbol} {interval} "
            f"[{start_str} ~ {end_str}] (fetched from {fetch_start})"
        )

    fmt = '%Y-%m-%d %H:%M' if interval in ('15m', '30m', '1h', '4h') else '%Y-%m-%d'
    start_date = df.index[0].strftime(fmt)
    end_date   = df.index[-1].strftime(fmt)
    title = f"{sym_cfg['short']}USDT {iv_cfg['label']} K-Line\n{start_date} ~ {end_date}"

    macd_colors = get_macd_colors(df['hist'])
    apds = [
        mpf.make_addplot(df[f'ma{MA_PERIODS[0]}'], panel=0, color=MA_COLORS[0], width=1.2, label=f'MA{MA_PERIODS[0]}'),
        mpf.make_addplot(df[f'ma{MA_PERIODS[1]}'], panel=0, color=MA_COLORS[1], width=1.2, label=f'MA{MA_PERIODS[1]}'),
        mpf.make_addplot(df[f'ma{MA_PERIODS[2]}'], panel=0, color=MA_COLORS[2], width=1.5, label=f'MA{MA_PERIODS[2]}'),
        mpf.make_addplot(df['macd'],   panel=2, color='#1f77b4', label='MACD'),
        mpf.make_addplot(df['signal'], panel=2, color='#ff7f0e', label='Signal'),
        mpf.make_addplot(df['hist'],   panel=2, type='bar', color=macd_colors),
    ]

    fig, axes = mpf.plot(
        df,
        type='candle',
        style='charles',
        title=title,
        ylabel='Price',
        volume=True,
        addplot=apds,
        panel_ratios=(4, 1, 2),
        figsize=(14, 10),
        returnfig=True,
        warn_too_much_data=TOO_MUCH_DATA_THRESHOLD,
    )
    fig.subplots_adjust(top=0.93)

    macd_ax = axes[4] if len(axes) >= 5 else None

    divergences = find_three_segment_divergences(
        df['hist'], df['low'], df['high'],
        min_bars=iv_cfg['min_bars'],
        max_level=iv_cfg['max_level'],
    )
    if macd_ax is not None:
        annotate_divergences(macd_ax, df, divergences)

    return fig, df, divergences


def make_output_filename(symbol, interval, df):
    sym_cfg = _resolve_symbol_config(symbol)
    iv_cfg  = INTERVAL_CONFIG[interval]
    fmt = '%Y%m%d_%H%M' if interval in ('15m', '30m', '1h', '4h') else '%Y-%m-%d'
    start_date = df.index[0].strftime(fmt)
    end_date   = df.index[-1].strftime(fmt)
    return f"{sym_cfg['short'].lower()}_{iv_cfg['file_prefix']}_{start_date}_{end_date}.png"


# ── CLI entry point ───────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python plot_kline.py <symbol> <interval> [start_str] [end_str]")
        print(f"  symbol:   any Binance trading pair (e.g. BTCUSDT, DOGEUSDT)")
        print(f"  interval: {list(INTERVAL_CONFIG.keys())}")
        sys.exit(1)

    symbol    = sys.argv[1]
    interval  = sys.argv[2]
    start_str = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
    end_str   = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else None

    fig, df, divs = render_chart(symbol, interval, start_str, end_str)

    sym_cfg = _resolve_symbol_config(symbol)
    iv_cfg  = INTERVAL_CONFIG[interval]

    print(f"Range contains {len(df)} bars of {sym_cfg['short']} {iv_cfg['label']}; rendering all.")
    print(f"Render range: {df.index[0]} ~ {df.index[-1]}")

    out_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        make_output_filename(symbol, interval, df),
    )
    fig.savefig(out_path, bbox_inches='tight', pad_inches=0.8)
    plt.close(fig)

    print_divergences(df, divs)
    print(f"Image saved: {out_path}")
    print(f"BARS={len(df)}")
