"""
获取 Hyperliquid K 线数据 - HYPE / PURR 等 HL 原生币的数据源
==============================================================

为什么需要这个 adapter
----------------------
HYPE 不在 binance.com 现货上市，python-binance 调用 get_historical_klines
拉 HYPEUSDT 会直接报 "Invalid symbol"。Hyperliquid 是 HYPE 的发行链 + 主要
交易场所，自家 info API 的 candleSnapshot 端点提供官方 K 线，是 HYPE
最权威的数据源。

接口约定与 data_binance.get_klines 完全同构：
    get_klines(symbol, interval, start_str, end_str=None) -> DataFrame
让 data.py 路由层无差别 fallback。

数据源特性
----------
- POST https://api.hyperliquid.xyz/info，免 API key，免注册
- 单次返回最多 5000 根（远超画图最大需求 ~356 根，无需分页）
- 支持 interval: 1m/3m/5m/15m/30m/1h/2h/4h/8h/12h/1d/3d/1w/1M
  本项目用到的 15m/30m/1h/4h/daily/3day/weekly 全覆盖
- 时间戳 UTC epoch millis；返回字段 t/T/o/h/l/c/v/n/i/s
- candleSnapshot 有每 60 项额外 weight 的 rate limit，画图量级完全无压

symbol 命名约定
---------------
对外仍用 BTCUSDT / HYPEUSDT / PURRUSDT 这种 binance 风格，内部剥掉
USDT 后缀得到 HL 的裸 coin 名（HL 现货代码就是裸 coin 名，不带 quote）。
这样 settings.py / UI 层不用为 HL 币种特别开一套命名约定。

为什么作为 binance 的 fallback 而不是单独路由
---------------------------------------------
data.py 路由层已有完善的 fallback 机制：adapter 抛异常会自动 catch 并
尝试下一个。binance 对不存在的 symbol（如 HYPEUSDT）会抛
BinanceAPIException("Invalid symbol")，正好命中 fallback。这样不用
维护"哪些币只在 HL"的白名单，新上 HL 原生币零配置自动支持。

副作用：对 binance 上确实存在的币（BTC/ETH/SOL...），主源直接命中，
fallback 永不触发，零开销。

懒加载
------
requests 在 module-level import，但 import 本身不发请求。HL 是无状态
POST，不需要类似 data_binance 那种 lazy client 模式。
"""
import datetime as _dt
import pandas as pd

try:
    import requests
except ImportError as e:
    raise ImportError(
        "data_hyperliquid 需要 requests 包：pip install requests"
    ) from e


_ENDPOINT = "https://api.hyperliquid.xyz/info"
_TIMEOUT = 15  # 秒。HL API 通常 < 1s 响应，留足余量给跨境网络


# ── interval 字符串 → HL interval ──────────────────────────────────
# key 与 plot_kline.py / navigation.py / data_binance.INTERVAL_MAP 一致
INTERVAL_MAP = {
    '15m':    '15m',
    '30m':    '30m',
    '1h':     '1h',
    '4h':     '4h',
    'daily':  '1d',
    '3day':   '3d',
    'weekly': '1w',
}


def _parse_to_ms(s):
    """
    把项目统一的日期字符串转为 UTC epoch millis。

    支持两种格式（与 data_binance 接受的字符串一致）：
        '17 Aug, 2017'
        '17 Aug, 2017 00:00:00'
    """
    for fmt in ('%d %b, %Y %H:%M:%S', '%d %b, %Y'):
        try:
            dt = _dt.datetime.strptime(s, fmt)
            return int(dt.replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)
        except ValueError:
            continue
    raise ValueError(
        f"无法解析日期字符串: {s!r}（期望 '17 Aug, 2017' 或带 HH:MM:SS 后缀）"
    )


def _strip_usdt(symbol):
    """
    'HYPEUSDT' → 'HYPE'。HL 现货代码就是裸 coin 名。
    已经是裸名（不带 USDT 后缀）就原样返回，兼容直接传 'HYPE' 的调用。
    """
    return symbol[:-4] if symbol.endswith('USDT') else symbol


def _fetch_candles(coin, hl_interval, start_ms, end_ms):
    """
    底层 API 调用。返回原始 candles list（HL 字段：t/T/o/h/l/c/v/n/i/s）。

    HL 端对不存在的 coin 不返回错误码，而是 200 OK + []。空列表交给
    get_klines 抛 ValueError，data.py 路由层据此判断 fallback 失败。
    """
    resp = requests.post(
        _ENDPOINT,
        json={
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": hl_interval,
                "startTime": start_ms,
                "endTime": end_ms,
            },
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json() or []


def get_klines(symbol, interval, start_str, end_str=None):
    """
    统一入口。symbol 例 'HYPEUSDT'，interval 例 'weekly'。

    所有周期按 [start_str, end_str] 精确拉取。end_str=None 表示拉到当下。
    返回 DataFrame：index=tz-naive DatetimeIndex（UTC 剥时区），
    columns=['open','high','low','close','volume']，全 float64。
    """
    if interval not in INTERVAL_MAP:
        raise ValueError(
            f"Unknown interval: {interval!r}. "
            f"Valid: {list(INTERVAL_MAP.keys())}"
        )

    coin = _strip_usdt(symbol)
    start_ms = _parse_to_ms(start_str)
    if end_str:
        end_ms = _parse_to_ms(end_str)
    else:
        end_ms = int(_dt.datetime.now(_dt.timezone.utc).timestamp() * 1000)

    candles = _fetch_candles(coin, INTERVAL_MAP[interval], start_ms, end_ms)

    if not candles:
        raise ValueError(
            f"Hyperliquid 无 {coin} {interval} 数据 "
            f"[{start_str} ~ {end_str or 'now'}]"
            f"（可能 coin 名不对或请求时段早于 HL 上线）"
        )

    df = pd.DataFrame(candles)
    # HL 字段映射 → 项目统一 schema
    # df['t'] 是 open_time ms，转 datetime 后是 tz-naive，本质为 UTC，
    # 跟 data_binance / data_yfinance 的 index 时区语义保持一致。
    df['open_time'] = pd.to_datetime(df['t'], unit='ms')
    for col in ('o', 'h', 'l', 'c', 'v'):
        df[col] = pd.to_numeric(df[col])
    df = df.rename(columns={
        'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close', 'v': 'volume',
    })
    df = df.set_index('open_time')
    df = df[['open', 'high', 'low', 'close', 'volume']]
    # HL 返回理论按 t 升序，但保险起见排一次（成本可忽略）
    df = df.sort_index()
    return df
