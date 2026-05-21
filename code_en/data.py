"""
Binance K-line data fetcher — parameterized version (precise on-demand fetch).

Design notes
------------
All intervals fetch precisely the [start_str, end_str] range passed by the
caller. There is no full-history cache and no "long interval vs short
interval" branching.

Why no cache
------------
The total number of K-lines required for a chart equals
    (target window bars) + LOOKBACK_BARS (200, to let MA99 and MACD stabilize)
Within the time ranges typically used by the UI, this total never exceeds
Binance's 1000-bar single-request limit:

    weekly  3-year window  ≈ 156 + 200 = 356  bars
    3day    1-year window  ≈ 122 + 200 = 322  bars
    daily   4-month window ≈ 120 + 200 = 320  bars
    4h      drilled window ≈ a few hundred + 200 bars
    1h/30m/15m             same order of magnitude

A single HTTP call ≈ 0.3 to 1 second — simpler and faster than maintaining
a 3240-bar cache with 4-page pagination, and avoids cache-invalidation
issues under main.py's subprocess execution model.

If at some point a caller needs to fetch a window >1000 bars, the
python-binance get_historical_klines function paginates internally
(splitting in 1000-bar chunks and concatenating). No extra handling is
needed — just more HTTP calls.
"""
from binance.client import Client
import pandas as pd

client = Client()


# ── interval string → Binance KLINE constant ─────────────────────────
# Keys are kept consistent with plot_kline.py / navigation.py.
INTERVAL_MAP = {
    '15m':    Client.KLINE_INTERVAL_15MINUTE,
    '30m':    Client.KLINE_INTERVAL_30MINUTE,
    '1h':     Client.KLINE_INTERVAL_1HOUR,
    '4h':     Client.KLINE_INTERVAL_4HOUR,
    'daily':  Client.KLINE_INTERVAL_1DAY,
    '3day':   Client.KLINE_INTERVAL_3DAY,
    'weekly': Client.KLINE_INTERVAL_1WEEK,
}


def _fetch_klines(symbol, interval, start_str, end_str):
    """
    Low-level API call. Returns a DataFrame (index=open_time, columns=ohlcv).

    start_str / end_str are date strings accepted by Binance, e.g.
    '17 Aug, 2017 00:00:00'. Pass end_str=None to fetch up to "now".
    """
    klines = client.get_historical_klines(
        symbol=symbol,
        interval=interval,
        start_str=start_str,
        end_str=end_str,
    )
    df = pd.DataFrame(klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "count",
        "taker_buy_base", "taker_buy_quote", "ignore"
    ])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])
    df = df.set_index("open_time")
    return df[["open", "high", "low", "close", "volume"]]


def get_klines(symbol, interval, start_str, end_str=None):
    """
    Unified entry point. e.g. symbol='BTCUSDT', interval='weekly'.

    All intervals fetch precisely the [start_str, end_str] range passed by
    the caller. end_str=None means fetch up to the current time.
    """
    if interval not in INTERVAL_MAP:
        raise ValueError(
            f"Unknown interval: {interval!r}. "
            f"Valid: {list(INTERVAL_MAP.keys())}"
        )
    return _fetch_klines(symbol, INTERVAL_MAP[interval], start_str, end_str)


# ── Backward-compatible thin wrappers: keep old API to avoid breaking
#    external scripts ────────────────────────────────────────────────
def get_weekly_klines(symbol="BTCUSDT", start_str="17 Aug, 2017", end_str=None):
    return get_klines(symbol, 'weekly', start_str, end_str)


def get_3day_klines(symbol="BTCUSDT", start_str="17 Aug, 2017", end_str=None):
    return get_klines(symbol, '3day', start_str, end_str)
