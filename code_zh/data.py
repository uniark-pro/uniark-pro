"""
获取 Binance K 线数据 - 参数化版本（按需精确拉取）

设计要点
--------
所有周期统一按调用方传入的 [start_str, end_str] 区间精确拉取。
没有全历史缓存、没有"长周期 vs 短周期"分支。

为什么不缓存
-----------
绘图所需的 K 线总数 = 目标时段根数 + LOOKBACK_BARS（200 根，让 MA99
和 MACD 稳定）。在调用方约定的时段范围内，这个总数永远不超过 Binance
单次 API 的 1000 根上限：

    weekly  3 年时段  ≈ 156 + 200 = 356  根
    3day    1 年时段  ≈ 122 + 200 = 322  根
    daily   4 月时段  ≈ 120 + 200 = 320  根
    4h      钻取段    ≈ 几百根  + 200    根
    1h/30m/15m       同理在量级内

一次 HTTP 调用 ≈ 0.3~1 秒，比"拉全历史 3240 根 + 4 次分页 + 缓存维护"
更简单、更快，也省去 main.py subprocess 模式下缓存失效的尴尬。

如果未来某天调用方要拉超过 1000 根的窗口，python-binance 的
get_historical_klines 自带分页（按 1000 根切分自动多次拉），不用
额外处理；只是 HTTP 次数会多。
"""
from binance.client import Client
import pandas as pd

client = Client()


# ── interval 字符串 → Binance KLINE 常量 ─────────────────────────────
# key 与 plot_kline.py / navigation.py 保持一致。
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
    底层 API 调用。返回 DataFrame（index=open_time，列=ohlcv）。

    start_str / end_str 是 Binance 接受的日期字符串，例
    '17 Aug, 2017 00:00:00'。end_str 传 None 表示"拉到现在"。
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
    统一入口。symbol 例如 'BTCUSDT'，interval 例如 'weekly'。

    所有周期都按调用方传入的 [start_str, end_str] 精确拉取。
    end_str=None 表示拉到当下。
    """
    if interval not in INTERVAL_MAP:
        raise ValueError(
            f"Unknown interval: {interval!r}. "
            f"Valid: {list(INTERVAL_MAP.keys())}"
        )
    return _fetch_klines(symbol, INTERVAL_MAP[interval], start_str, end_str)


# ── 向后兼容的薄包装：保留旧 API，避免外部脚本破裂 ───────────────────
def get_weekly_klines(symbol="BTCUSDT", start_str="17 Aug, 2017", end_str=None):
    return get_klines(symbol, 'weekly', start_str, end_str)


def get_3day_klines(symbol="BTCUSDT", start_str="17 Aug, 2017", end_str=None):
    return get_klines(symbol, '3day', start_str, end_str)
