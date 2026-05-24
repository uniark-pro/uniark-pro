"""
高周期投影到低周期 - 导航逻辑(双 market 版,背离钻取模式)
=============================================================
"地图 → 放大镜" 工作流的核心抽象。

工作流:
  1. 用户选 market → 选标的 → 选入口周期 → 选时段 → 看图
  2. 当前图上检测到的每条背离 = 一个钻取入口卡片
  3. 点击某条背离 = 用该背离的极值时间 (peak_iso) 锚定,钻到下一级周期
     窗口 = peak_iso ± (BEFORE, AFTER) 根次级 K 线时长
  4. 重复直到金字塔末端(crypto 是 15m,三个 stock market 都是 1h)

钻取窗口公式:
  fetch_start = peak_iso - BEFORE × next_interval_minutes × trading_factor
  fetch_end   = peak_iso + AFTER  × next_interval_minutes × trading_factor

  BEFORE > AFTER:大头放在 peak 之前,因为我们看的是"什么导致了这次极值",
  AFTER 留一段是给后续验证/印证用,过小则刚翻号就没了。

  trading_factor: crypto = 1.0 (24/7 连续);stock daily/weekly = 1.0
  (yfinance/akshare 只返交易日 bar,节假日不补);stock intraday (1h 等) =
  (24 / 交易小时数) × (7 / 5),A 股 1h 因子最大达 ~8.4。详见
  compute_lookback_factor。同一个 factor 函数也被 plot_kline._compute_fetch_start
  用来放大 lookback,两处保持一致。

为什么不再做"等距切段"(旧设计):
  等距切段把当前窗口机械地切成 N 段,每段一个按钮——但这些段未必对应
  有意义的事件,大部分点击都是"碰运气"。背离钻取每个入口都是一个具体
  的市场结构信号(极值 + 力度衰竭),点击意图明确。

market 差异
-----------
crypto 的金字塔覆盖到 15m(币圈 24/7 连续)。
三个 stock market(美股/A股/港股)共用同一条钻取链 weekly→daily→1h,
yfinance 1h 原生支持,能往回拿 ~730 天数据。更小级别(30m/15m/5m)
受 yfinance 60 天硬墙限制,留作后续扩展。
"""
import datetime as _dt
import pandas as pd
from config import DIVERGENCE_DRILL_BARS_BEFORE, DIVERGENCE_DRILL_BARS_AFTER


# ── 周期金字塔（按 market 分支）─────────────────────────────────────
# 三个 stock market（美股/A股/港股）共用同一套钻取链。
# weekly 下钻到 3day，3day 下钻到 daily，daily 下钻到 1h。
# 3day 自身由 adapter 用 daily 重采样合成（akshare / yfinance 均已实现）。
_STOCK_CHAIN = {
    'weekly': '3day',       # 周线下钻到 3 日线
    '3day':   'daily',      # 3 日线下钻到日线
    'daily':  '1h',         # 钻取末端：从 daily 进一步钻到小时级
    '1h':     None,         # yfinance 1h 有 730 天窗口硬墙；更小级别不开放
}

NEXT_INTERVAL_BY_MARKET = {
    'crypto': {
        'weekly': '3day',
        '3day':   'daily',
        'daily':  '4h',
        '4h':     '1h',
        '1h':     '30m',
        '30m':    '15m',
        '15m':    None,
    },
    'us_stock': dict(_STOCK_CHAIN),
    'cn_stock': dict(_STOCK_CHAIN),
    'hk_stock': dict(_STOCK_CHAIN),
}

# 旧代码可能直接 import NEXT_INTERVAL（指 crypto 的金字塔）。保留作为
# 兼容别名。新代码请用 next_interval(interval, market) 函数。
NEXT_INTERVAL = NEXT_INTERVAL_BY_MARKET['crypto']


# 各周期单根 K 线代表的分钟数。从这里推算周期比，永远是物理事实。
INTERVAL_MINUTES = {
    '15m':       15,
    '30m':       30,
    '1h':        60,
    '4h':        240,
    'daily':     1440,
    '3day':      4320,
    'weekly':    10080,
}


# ── 交易日因子配置 ──────────────────────────────────────────────────
# 给 stock 盘中粒度（1h 等）做"物理时间 → 实际 K 线根数"换算用。
#
# 问题来源：钻取窗口 / lookback 都按 "N 根 K 线 × 单根 K 线分钟数" 推物理时间，
# 这在 crypto 24/7 市场没问题（200 物理小时 = 200 根 1h K 线），但 stock 1h
# 每个交易日只有几小时交易，物理时间往前推 200 小时只能拿到 ~24 根 1h K 线，
# 严重低估窗口（MA99 缺失、钻取窗口比预期窄 6-8 倍）。
#
# 修正：在物理时间上乘以 (24 / 交易小时数) × (7 / 5)
#                            │     日内交易窗口拉伸    │ 周末日历日补偿
#
#   - A 股: 09:30-11:30 + 13:00-15:00 = 4 小时 / 日（最短，午休 1.5h）
#   - 港股: 09:30-12:00 + 13:00-16:00 = 5.5 小时 / 日
#   - 美股: 09:30-16:00 = 6.5 小时 / 日（连续无午休）
# 任何 stock market 未在此表注册，按 6.5（美股值）做安全估计。
TRADING_HOURS_PER_DAY = {
    'us_stock': 6.5,
    'hk_stock': 5.5,
    'cn_stock': 4.0,
}

# 需要做交易日因子换算的 intraday 粒度。daily / weekly / 3day 不在此列：
# 这些粒度本身就以"交易日"为单位，yfinance/akshare 不补节假日 bar，按物理时间
# 推 200 根的时长，得到的实际 K 线根数已 ≈ N。
INTRADAY_INTERVALS = ('15m', '30m', '1h', '4h')


def compute_lookback_factor(market, interval):
    """
    返回 "N 根 K 线物理时间" 的放大倍数。

    crypto 任意 interval / stock 的 daily 及以上：factor = 1（不放大）
    stock 的 intraday 粒度：factor = (24 / 交易小时数) × (7 / 5)

    这个函数同时被两处调用，保持一致：
      - plot_kline._compute_fetch_start: 放大 LOOKBACK_BARS 的物理跨度，
        让 MA99/MACD 在 start_str 处稳定
      - navigation.compute_divergence_drill_window: 放大背离钻取的
        BEFORE / AFTER 窗口，让钻取后图上能看到完整 585 根次级 K 线
    """
    if market == 'crypto':
        return 1.0
    if interval not in INTRADAY_INTERVALS:
        return 1.0
    hours_per_day = TRADING_HOURS_PER_DAY.get(market, 6.5)
    return (24.0 / hours_per_day) * (7.0 / 5.0)


# ── 顶级入口：周线 4 个 3 年段（仅供历史代码引用，新代码用 settings）─────
TOP_RANGES = [
    {'label': '2017-08 ~ 2020-05', 'start': '17 Aug, 2017', 'end': '30 May, 2020'},
    {'label': '2020-03 ~ 2022-12', 'start': '17 Mar, 2020', 'end': '30 Dec, 2022'},
    {'label': '2022-10 ~ 2025-10', 'start': '17 Oct, 2022', 'end': '30 Oct, 2025'},
    {'label': '2025-04 ~ 2027-11', 'start': '17 Apr, 2025', 'end': '30 Nov, 2027'},
]


# ── 背离钻取的窗口大小 ─────────────────────────────────────────────
# 钻取到 next_interval 时,从极值时间 peak_iso 向两侧推:
#   - BEFORE 根:peak 之前,显示"是什么导致了这次极值"
#   - AFTER  根:peak 之后,印证/确认极值是否成立 + 容纳 S_last 在 peak 之后
#                的延伸 + 父级一根 K 线在子级别上的物理时长
# 总窗口 = BEFORE + 1(peak 本身)+ AFTER = 286 根次级 K 线。
# BEFORE 大于 AFTER 因为信号的形成过程更值得回看。
#
# 窗口收窄到 286(早期曾用 585)
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~
# 更窄的窗口让目标背离结构占据画面主体,不被前后大量无关的宏观结构淹没;
# 同时数据量更小,对 yfinance 1h 的 730 天硬墙也更安全。
#


# ── 主算法 ──────────────────────────────────────────────────────────
def next_interval(interval, market='crypto'):
    """返回当前周期的下一级周期；末端返回 None。"""
    if market not in NEXT_INTERVAL_BY_MARKET:
        raise ValueError(f"未知 market: {market!r}")
    return NEXT_INTERVAL_BY_MARKET[market].get(interval)


def has_drilldown(interval, market='crypto'):
    """该周期在该 market 下是否还能继续钻取。"""
    return next_interval(interval, market) is not None


def compute_divergence_drill_window(peak_ts, next_iv, market='crypto'):
    """
    给定背离极值时间 + 目标钻取周期,计算钻取窗口的起止时间。

    Parameters
    ----------
    peak_ts : pd.Timestamp | datetime
        背离极值时间(底背离 = K 线最低价时间,顶背离 = K 线最高价时间)
    next_iv : str
        目标钻取周期,如 'daily' / '4h' / '1h' 等
    market : str
        所属 market（'crypto' / 'us_stock' / 'cn_stock' / 'hk_stock'）。
        默认 'crypto' 是为了向后兼容老调用方,但对 stock + intraday 组合
        必须显式传入,否则 stock 1h 钻取窗口会被压缩 5-8 倍。
        所有产用调用方（main.py / app.py JS / api 层）都应当传入实际 market。

    Returns
    -------
    (pd.Timestamp, pd.Timestamp)
        (window_start, window_end) 钻取窗口
    """
    if not isinstance(peak_ts, pd.Timestamp):
        peak_ts = pd.Timestamp(peak_ts)
    if next_iv not in INTERVAL_MINUTES:
        raise ValueError(f"未知 interval: {next_iv!r}")
    minutes = INTERVAL_MINUTES[next_iv]
    factor = compute_lookback_factor(market, next_iv)
    before = _dt.timedelta(minutes=minutes * DIVERGENCE_DRILL_BARS_BEFORE * factor)
    after  = _dt.timedelta(minutes=minutes * DIVERGENCE_DRILL_BARS_AFTER  * factor)
    return peak_ts - before, peak_ts + after


def format_range_label(start_ts, end_ts, interval):
    """时间窗格式化成 UI 可读标签。短周期带时分。"""
    if not isinstance(start_ts, pd.Timestamp):
        start_ts = pd.Timestamp(start_ts)
    if not isinstance(end_ts, pd.Timestamp):
        end_ts = pd.Timestamp(end_ts)
    fmt = '%m-%d %H:%M' if interval in ('15m', '30m', '1h', '4h') else '%Y-%m-%d'
    return f"{start_ts.strftime(fmt)} ~ {end_ts.strftime(fmt)}"


def to_binance_str(ts):
    """pd.Timestamp / datetime → binance 风格日期字符串"""
    if not isinstance(ts, pd.Timestamp):
        ts = pd.Timestamp(ts)
    return ts.strftime('%d %b, %Y %H:%M:%S')
