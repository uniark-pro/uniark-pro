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
  fetch_start = peak_iso - BEFORE × next_interval_minutes
  fetch_end   = peak_iso + AFTER  × next_interval_minutes
  
  BEFORE >> AFTER:大头放在 peak 之前,因为我们看的是"什么导致了这次极值",
  AFTER 留一段是给后续验证/印证用,过小则刚翻号就没了。

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


# ── 周期金字塔（按 market 分支）─────────────────────────────────────
# 三个 stock market（美股/A股/港股）共用同一套钻取链 weekly→daily→1h→None。
# 它们的差异只体现在 ticker 后缀（无后缀/.SS/.SZ/.HK），navigation 层不区分。
_STOCK_CHAIN = {
    'weekly': 'daily',
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
#   - AFTER  根:peak 之后,印证/确认极值是否成立
# 总窗口 = BEFORE + 1(peak 本身)+ AFTER = 485 根次级 K 线。
# BEFORE 远大于 AFTER 因为信号的形成过程更值得回看。
DIVERGENCE_DRILL_BARS_BEFORE = 354
DIVERGENCE_DRILL_BARS_AFTER  = 130


# ── 主算法 ──────────────────────────────────────────────────────────
def next_interval(interval, market='crypto'):
    """返回当前周期的下一级周期；末端返回 None。"""
    if market not in NEXT_INTERVAL_BY_MARKET:
        raise ValueError(f"未知 market: {market!r}")
    return NEXT_INTERVAL_BY_MARKET[market].get(interval)


def has_drilldown(interval, market='crypto'):
    """该周期在该 market 下是否还能继续钻取。"""
    return next_interval(interval, market) is not None


def compute_divergence_drill_window(peak_ts, next_iv):
    """
    给定背离极值时间 + 目标钻取周期,计算钻取窗口的起止时间。

    Parameters
    ----------
    peak_ts : pd.Timestamp | datetime
        背离极值时间(底背离 = K 线最低价时间,顶背离 = K 线最高价时间)
    next_iv : str
        目标钻取周期,如 'daily' / '4h' / '1h' 等

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
    before = _dt.timedelta(minutes=minutes * DIVERGENCE_DRILL_BARS_BEFORE)
    after  = _dt.timedelta(minutes=minutes * DIVERGENCE_DRILL_BARS_AFTER)
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
