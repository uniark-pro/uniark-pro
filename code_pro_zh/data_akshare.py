"""
akshare 数据 adapter - stock fallback
======================================

设计要点
--------
跟 data_binance / data_yfinance 同构的接口签名，由 data.py 在 yfinance
返回数据不足（< MIN_BARS_FOR_PRIMARY 行）时自动 fallback 调过来。

为什么要 akshare 兜底
---------------------
yfinance 1.3.0 + Yahoo 后端在 2025-2026 年持续退化：
  - 新上市港股（如 0100.HK MiniMax）拉不到历史数据，silent 返 0/1 行
  - 主流标的（AAPL/TSLA/^GSPC）也时常 "possibly delisted" 错误
  - 社区怀疑 Yahoo 把历史数据访问限制成付费订阅

akshare 走的是中文数据源（新浪/东财/腾讯），跟 Yahoo 完全独立，对
A 股/港股的覆盖比 yfinance 更全更及时。

支持的粒度
----------
  - daily : 三个 market 都支持
  - weekly: 通过 daily → 内部重采样合成（akshare 无原生 weekly）
  - 1h    : 暂不支持（akshare 港股/美股的盘中数据接口不稳定，留作后续）

ticker 格式转换
---------------
项目内统一用 yfinance 风格 ticker（含 .SS / .SZ / .HK 后缀），
akshare 用裸代码无后缀。adapter 入口做转换：
  - 600519.SS / 600519.SZ → '600519'（akshare 自动按代码识别交易所）
  - 0100.HK → '00100'（akshare 港股要求 5 位）
  - MU → 'MU'（美股原样）

复权
----
统一用 'qfq'（前复权），跟 yfinance 的 auto_adjust=True 语义一致：
返回的 close 直接是连续复权价，可喂给 MACD 不出台阶式假信号。
"""
import datetime as _dt
import pandas as pd

try:
    import akshare as ak
except ImportError as e:
    raise ImportError(
        "data_akshare 需要 akshare 包。请执行：pip install akshare"
    ) from e


# ── interval 字符串 → akshare 周期参数 ─────────────────────────────
# A 股 stock_zh_a_hist 直接支持 daily/weekly；港股/美股的 daily 接口
# 只给日线，weekly 由 adapter 内部从 daily 重采样合成。
INTERVAL_MAP_CN = {
    'daily':  'daily',
    'weekly': 'weekly',
}

# ── 对外接口 ────────────────────────────────────────────────────────
def get_klines(symbol, interval, start_str, end_str=None):
    """
    与 data_yfinance.get_klines 同签名。

    Parameters
    ----------
    symbol : str
        yfinance 风格 ticker：'600519.SS' / '0100.HK' / 'MU' / 'AAPL'
    interval : str
        'daily' | 'weekly'。1h 暂不支持，抛 NotImplementedError。
    start_str : str
        '17 Aug, 2017' 风格日期字符串（含可选 ' HH:MM:SS'）
    end_str : str | None
        同上；None 表示拉到当下

    Returns
    -------
    pd.DataFrame
        index=tz-naive DatetimeIndex，列=['open','high','low','close','volume']
        全 float64。close 为前复权价格。
    """
    if interval not in ('daily', 'weekly'):
        raise NotImplementedError(
            f"akshare adapter 仅支持 daily / weekly，收到 {interval!r}。"
            f"1h 留作后续扩展。"
        )

    start_dt = _parse_date_str(start_str)
    end_dt = _parse_date_str(end_str) if end_str else _dt.datetime.now()
    # akshare 用紧凑日期格式 'YYYYMMDD'
    start_compact = start_dt.strftime('%Y%m%d')
    end_compact = end_dt.strftime('%Y%m%d')

    sym_upper = symbol.upper()
    if sym_upper.endswith('.SS') or sym_upper.endswith('.SZ'):
        df = _fetch_cn_stock(sym_upper, interval, start_compact, end_compact)
    elif sym_upper.endswith('.HK'):
        df = _fetch_hk_stock(sym_upper, interval, start_compact, end_compact)
    else:
        df = _fetch_us_stock(sym_upper, interval, start_compact, end_compact)

    return df


# ── 各 market 的 fetch 实现 ─────────────────────────────────────────
def _fetch_cn_stock(symbol, interval, start_compact, end_compact):
    """A 股：东财接口 stock_zh_a_hist 直接支持 daily/weekly + 起止日期"""
    # 600519.SS → '600519'，akshare 内部自动识别交易所
    code = symbol[:-3]   # 去掉 '.SS' / '.SZ'
    df = ak.stock_zh_a_hist(
        symbol=code,
        period=INTERVAL_MAP_CN[interval],
        start_date=start_compact,
        end_date=end_compact,
        adjust='qfq',     # 前复权
    )
    return _normalize_cn_df(df)


def _fetch_hk_stock(symbol, interval, start_compact, end_compact):
    """港股：stock_hk_daily 只给 daily，weekly 由 adapter 重采样"""
    # 0100.HK → '00100'。akshare 港股要求 5 位代码（前导零）
    code = symbol[:-3]   # 去掉 '.HK'
    if code.isdigit() and len(code) < 5:
        code = code.zfill(5)
    # akshare 港股 stock_hk_daily 不接受 start_date / end_date 参数，
    # 一次性返回该股全部历史，本地切片后再返回
    df = ak.stock_hk_daily(symbol=code, adjust='qfq')
    df = _normalize_hk_us_df(df)
    df = _slice_by_date(df, start_compact, end_compact)
    if interval == 'weekly':
        df = _resample_to_weekly(df)
    return df


def _fetch_us_stock(symbol, interval, start_compact, end_compact):
    """美股：stock_us_daily 只给 daily，weekly 由 adapter 重采样"""
    # 美股 ticker 原样
    df = ak.stock_us_daily(symbol=symbol, adjust='qfq')
    df = _normalize_hk_us_df(df)
    df = _slice_by_date(df, start_compact, end_compact)
    if interval == 'weekly':
        df = _resample_to_weekly(df)
    return df


# ── DataFrame 规整 ────────────────────────────────────────────────
def _normalize_cn_df(df):
    """
    A 股 stock_zh_a_hist 返回的列名是中文，需要映射到项目统一的小写英文列。
    返回前重置索引为 tz-naive DatetimeIndex。
    """
    if df is None or df.empty:
        return _empty_df()
    rename_map = {
        '日期': 'date',
        '开盘': 'open',
        '最高': 'high',
        '最低': 'low',
        '收盘': 'close',
        '成交量': 'volume',
    }
    df = df.rename(columns=rename_map)
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date')
    df.index.name = None
    needed = ['open', 'high', 'low', 'close', 'volume']
    df = df[needed].copy()
    for c in needed:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['open', 'high', 'low', 'close'])
    return df


def _normalize_hk_us_df(df):
    """港股 / 美股的 daily 接口列名已是英文，但首列是 'date' 需要设为索引"""
    if df is None or df.empty:
        return _empty_df()
    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date')
        df.index.name = None
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    needed = ['open', 'high', 'low', 'close', 'volume']
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise RuntimeError(
            f"akshare 返回数据缺少列 {missing}，实有 {list(df.columns)}"
        )
    df = df[needed].copy()
    for c in needed:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['open', 'high', 'low', 'close'])
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


# ── 工具 ────────────────────────────────────────────────────────────
def _parse_date_str(s):
    s = s.strip()
    for fmt in ('%d %b, %Y %H:%M:%S', '%d %b, %Y'):
        try:
            return _dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析日期字符串: {s!r}（期望格式 '17 Aug, 2017'）")


def _slice_by_date(df, start_compact, end_compact):
    """按 'YYYYMMDD' 字符串过滤 DataFrame 到 [start, end] 区间"""
    if len(df) == 0:
        return df
    s = pd.Timestamp(start_compact)
    e = pd.Timestamp(end_compact)
    return df[(df.index >= s) & (df.index <= e)]


def _resample_to_weekly(df):
    """
    把 daily DataFrame 重采样为 weekly。
    OHLCV 聚合规则：Open=first / High=max / Low=min / Close=last / Volume=sum
    用 'W-FRI' 让每根 weekly K 线锚定到周五（跟 yfinance 的 weekly 行为一致）。
    """
    if len(df) == 0:
        return df
    return df.resample('W-FRI').agg({
        'open':   'first',
        'high':   'max',
        'low':    'min',
        'close':  'last',
        'volume': 'sum',
    }).dropna(subset=['open', 'high', 'low', 'close'])


def _empty_df():
    """返回空 DataFrame 但列结构完整，避免上层 df.index >= ts 比较时崩"""
    empty = pd.DataFrame(
        {c: pd.Series(dtype='float64')
         for c in ('open', 'high', 'low', 'close', 'volume')}
    )
    empty.index = pd.DatetimeIndex([], name=None)
    return empty
