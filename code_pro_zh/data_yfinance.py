"""
获取 yfinance K 线数据 - 美股 adapter
=====================================

设计要点
--------
跟 data_binance.py 完全同构的接口签名，由 data.py 路由层根据 market
分发调用。返回值的 DataFrame 结构（index=时间戳、列=open/high/low/close/volume）
与 binance adapter 一致，让上层算法（indicator/divergence/plot）保持
数据无关。

复权（Adj Close）
-----------------
yfinance 默认 close 是未复权价，这会让 MACD 在拆股日和分红日出现
"假信号"——价格台阶式跳变，hist 序列被污染，背离判定失真。
所以本 adapter 强制 auto_adjust=True，让返回的 close 直接是
复权后连续序列。原始 close 我们用不到，舍弃。

时区
----
统一返回 tz-naive 的 DatetimeIndex（UTC 时间剥掉 tz 信息），跟
binance adapter 的"naive、本质上 UTC"行为一致。yfinance 默认会
给带时区的 index（盘中级别带交易所本地时区，日级及以上有的版本带 UTC），
这里统一规整。

第二阶段支持的粒度
------------------
支持 weekly、daily、1h 三个粒度——这与 settings 的 stock entry intervals
（weekly/daily）+ navigation 末端钻取 1h 一致。其他粒度（3day / 4h /
30m / 15m）暂不开放，请求时会抛 NotImplementedError 提醒调用方。

  - 3day / 4h 需要重采样合成（yfinance 无原生粒度）
  - 30m / 15m / 5m yfinance 只能拉最近 60 天，且美股盘前盘后断点
    会让 K 线序列出现 gap，钻取语义需要单独设计

这些都留作后续扩展。

1h 数据的时间窗硬墙
-------------------
yfinance 对 1h 粒度只供应**距今 730 天内**的数据，更早的 1h 历史
拿不到（这是 yfinance 自身限制，与本 adapter 无关）。本 adapter 在请求
1h 数据前做窗口校验：若 start_str 早于 (now - 730d)，抛 ValueError
并给出明确建议。

1h 数据的时区
-------------
1h 粒度时间戳带交易所时区。本 adapter 根据 ticker 后缀决定时区：
  - .SS / .SZ          → Asia/Shanghai（A 股）
  - .HK                → Asia/Hong_Kong（港股）
  - 其他（含无后缀）   → America/New_York（美股）
然后剥时区保留本地时间——这样图上看到的"09:30 / 10:30 ..."直接就是
当地交易日盘中时间，符合用户直觉。
（daily/weekly 的 yfinance 索引本身就是 tz-naive 的当地日期 00:00，
不影响。）

A 股/港股 1h 的午休问题
-----------------------
A 股交易时间 09:30-11:30 + 13:00-15:00（午休 1.5 小时），yfinance
不会在午休时段补 NaN bar——直接拼接 09:30/10:30/13:30/14:30 这几根
1h K 线。本 adapter 不主动填充，让上层 indicator 把它们视作连续序列处理
（这跟 binance 数据的处理方式一致）。如果将来需要严格按物理时间对齐，
再单独引入填充逻辑。

日期格式兼容
------------
入参的 start_str / end_str 沿用项目统一的 binance 风格字符串
'17 Aug, 2017'（含可选的 ' HH:MM:SS' 后缀），内部解析后转给 yfinance。
end_str 传 None 表示拉到当下。
"""
import datetime as _dt
import pandas as pd

try:
    import yfinance as yf
except ImportError as e:
    raise ImportError(
        "data_yfinance 需要 yfinance 包。请执行：pip install yfinance"
    ) from e

# 屏蔽 yfinance 自己的 logger 噪音。yfinance 1.3.0+ 对每个拉不到数据
# 的 ticker 打 ERROR 级别 "$XXX: possibly delisted; no price data found"，
# 直接 print 到 stderr。我们有 fallback 兜底（data.py 路由层自动走 akshare），
# 这些假"退市"警告对用户而言纯属噪音——会让人误以为代码坏了，实际数据已经从
# akshare 正常拿到。把 yfinance logger 提到 CRITICAL 让它对 ERROR/WARNING
# 闭嘴。诊断真实问题时改 logging.INFO 即可。
import logging as _logging
_logging.getLogger('yfinance').setLevel(_logging.CRITICAL)


# ── interval 字符串 → yfinance interval 参数 ─────────────────────────
# 当前开放：weekly / daily / 1h（钻取末端）。其他粒度故意不映射，
# 一旦被请求会在 get_klines 里抛 NotImplementedError。
INTERVAL_MAP = {
    'daily':  '1d',
    'weekly': '1wk',
    '1h':     '1h',     # yfinance 也接受 '60m'，效果等价
}

# yfinance 对 1h 粒度的历史窗口硬墙：只供应距今 730 天内的数据。
# 留 5 天冗余防止边界查询触发"start too old"。
INTRADAY_LOOKBACK_LIMIT_DAYS = {
    '1h': 730 - 5,
}


def _parse_date_str(s):
    """
    解析项目里通用的 binance 风格日期字符串：
        '17 Aug, 2017'              → 2017-08-17 00:00:00
        '17 Aug, 2017 00:00:00'     → 2017-08-17 00:00:00
    解析失败抛 ValueError。
    """
    s = s.strip()
    for fmt in ('%d %b, %Y %H:%M:%S', '%d %b, %Y'):
        try:
            return _dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析日期字符串: {s!r}（期望格式 '17 Aug, 2017'）")


def _tz_for_symbol(symbol):
    """
    根据 ticker 后缀推断交易所时区。
      .SS / .SZ → 上海（A 股，时区跟北京时间一致）
      .HK       → 香港
      其他      → 美东（美股或无后缀的 yfinance ticker）
    后缀比较前先大写化，与 settings 里 _normalize_symbols 的规整一致。
    """
    s = symbol.upper()
    if s.endswith('.SS') or s.endswith('.SZ'):
        return 'Asia/Shanghai'
    if s.endswith('.HK'):
        return 'Asia/Hong_Kong'
    return 'America/New_York'


def _to_market_naive(df, interval, symbol):
    """
    把 DataFrame 的 DatetimeIndex 规整为 tz-naive。

    - intraday 粒度（1h 等）：先转到 ticker 对应的交易所时区，再剥时区，
      这样图上的小时刻度直接是当地交易日盘中时间，符合用户直觉。
        美股 → 09:30 / 10:30 / ... / 15:30
        A 股 → 09:30 / 10:30 / 13:30 / 14:30（午休跳过）
        港股 → 09:30 / 10:30 / ... / 15:30
    - daily / weekly：yfinance 给的索引通常已是 tz-naive 的当地日期 00:00，
      直接返回；如果意外带了 tz（不同 yfinance 版本行为不一），按 ticker
      所属交易所时区处理。
    """
    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex) or idx.tz is None:
        return df
    tz_name = _tz_for_symbol(symbol)
    df = df.copy()
    df.index = idx.tz_convert(tz_name).tz_localize(None)
    return df


def _validate_intraday_lookback(interval, start_dt):
    """
    校验 intraday 粒度（如 1h）的 start_dt 是否落在 yfinance 允许窗口内。
    超出则抛带建议的 ValueError。
    """
    limit_days = INTRADAY_LOOKBACK_LIMIT_DAYS.get(interval)
    if limit_days is None:
        return  # 该粒度无 intraday 限制（daily / weekly）
    earliest_allowed = _dt.datetime.now() - _dt.timedelta(days=limit_days)
    if start_dt < earliest_allowed:
        raise ValueError(
            f"yfinance 对 {interval} 粒度只供应距今 {limit_days} 天内的数据，"
            f"请求起点 {start_dt:%Y-%m-%d} 已超出窗口。"
            f"\n建议：把起始日期调整为 {earliest_allowed:%Y-%m-%d} 之后，"
            f"或改用 daily / weekly 粒度查看更早的历史。"
        )


def get_klines(symbol, interval, start_str, end_str=None):
    """
    统一入口，签名与 data_binance.get_klines 对齐。

    Parameters
    ----------
    symbol : str
        美股 ticker，例 'MU'、'AAPL'、'NVDA'。也支持 yfinance 兼容的
        其他后缀（'600519.SS' 上交所、'00700.HK' 港股）——能不能拉到
        数据由 yfinance 决定，本 adapter 不做白名单限制。
    interval : str
        'daily' | 'weekly' | '1h'。其他值抛 NotImplementedError。
        - daily / weekly：无历史长度限制
        - 1h：受 yfinance 730 天硬墙约束（早于该窗口的 start_str 会抛 ValueError）
    start_str : str
        起始日期，格式 '17 Aug, 2017'（含可选 ' HH:MM:SS' 后缀）。
    end_str : str | None
        结束日期，格式同 start_str。None 表示拉到当下。

    Returns
    -------
    pd.DataFrame
        index=DatetimeIndex（tz-naive；intraday 为美东本地时间），
        列=['open','high','low','close','volume'] 全部 float64。
        close 是复权后价格（auto_adjust=True 的 Close 列）。
    """
    if interval not in INTERVAL_MAP:
        raise NotImplementedError(
            f"美股 adapter 当前仅支持 {list(INTERVAL_MAP.keys())}，"
            f"收到 {interval!r}。3day/4h/30m/15m 留待后续扩展。"
        )

    start_dt = _parse_date_str(start_str)
    end_dt = _parse_date_str(end_str) if end_str else None

    # intraday 粒度的窗口校验（只对 1h 起效）
    _validate_intraday_lookback(interval, start_dt)

    # ── 未来 end 截到当下 ──
    # yfinance 对未来 end 日期处理不稳定（用 end > today 请求时偶发只返回
    # 最近 1 个交易日，而非完整序列）。这里在 adapter 层把任何未来 end
    # 截到 (today + 1d)，让 yfinance 拉到当下数据。语义上等价于"用户填的
    # 未来 end 表达 'lazy 一直拉到现在'"。
    today = _dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if end_dt is not None and end_dt > today:
        end_dt = today + _dt.timedelta(days=1)

    # yfinance 的 end 参数是"开区间"——传的日期当天的数据不会包含。
    # 为保证返回的窗口与 binance adapter 等效（含端点），把 end 加 1 天。
    yf_end = (end_dt + _dt.timedelta(days=1)) if end_dt else None

    # ── 用 Ticker.history() 而不是 yf.download() ──
    # yfinance 的 download() 函数对部分港股 ticker 有已知 bug：start/end
    # 参数合理时仍只返回最近 1 个交易日。参考 yfinance issue #1763。
    # Ticker.history() 走的是不同的内部路径（chart API 而非 spark API），
    # 在港股 ticker 上明显更可靠。我们统一用 history() 拉所有 stock 数据。
    df = yf.Ticker(symbol).history(
        start=start_dt,
        end=yf_end,
        interval=INTERVAL_MAP[interval],
        auto_adjust=True,     # 让 Close 列直接是复权价
    )

    if df is None or df.empty:
        # 让上层（data.py 路由层）走 fallback 链。返回空 DataFrame
        # 但结构完整，避免 df.index >= ts 比较时崩。
        empty = pd.DataFrame(
            {c: pd.Series(dtype='float64')
             for c in ('open', 'high', 'low', 'close', 'volume')}
        )
        empty.index = pd.DatetimeIndex([], name=None)
        return empty

    # 单 ticker 时 yfinance 新版本会返回 MultiIndex 列（('Close','MU')）
    # 老版本是单层列（'Close'）。统一压平到单层。
    if isinstance(df.columns, pd.MultiIndex):
        # 第二层是 ticker 名，第一层是字段名；保留第一层。
        df.columns = df.columns.get_level_values(0)

    # 列名小写化对齐 binance adapter
    rename_map = {
        'Open':   'open',
        'High':   'high',
        'Low':    'low',
        'Close':  'close',
        'Volume': 'volume',
    }
    df = df.rename(columns=rename_map)

    # 只保留我们需要的 5 列；缺哪列就报错（说明 yfinance 返回结构异常）
    needed = ['open', 'high', 'low', 'close', 'volume']
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise RuntimeError(
            f"yfinance 返回数据缺少列 {missing}，实有 {list(df.columns)}"
        )
    df = df[needed].copy()

    # 强制数值类型（防止某些情况下出现 object dtype）
    for col in needed:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    # tz 规整 + 丢弃任何 NaN 行（yfinance 偶尔会塞空 bar）
    df = _to_market_naive(df, interval, symbol)
    df = df.dropna(subset=['open', 'high', 'low', 'close'])

    return df
