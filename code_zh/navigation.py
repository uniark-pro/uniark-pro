"""
高周期投影到低周期 - 导航逻辑
================================
"地图 → 放大镜" 工作流的核心抽象。

工作流：
  1. 用户选币种 → 选周线时段（4 个 3 年段中的一个）→ 看周线图
  2. 周线图根据 K 线根数计算应切几段，每段用 3day 渲染（钻取）
  3. 重复直到 15m（金字塔末端，不再细分）

段数公式：
  N_next = 当前根数 × 周期比（当前周期分钟 ÷ 下一级周期分钟）
  段数   = floor(N_next / BARS_PER_SEGMENT_TARGET) + 1

直觉：每段下一级 K 线大致恒定在目标根数附近。BARS_PER_SEGMENT_TARGET 是
软性目标，决定"每张图想容纳多少根 K 线"。385 接近"一年多一点的日线窗口"。

边界：
  - 段数 ≥ 1（公式天然保证）。即使只切 1 段，UI 也照常显示按钮，
    点击才进入下一级 —— 保持"看图 → 选段 → 进下一级"的交互一致性。
  - 15m 是终点，无下一级。
"""
import datetime as _dt
import pandas as pd


# ── 周期金字塔（顶级 weekly → 终端 15m）─────────────────────────────
NEXT_INTERVAL = {
    'weekly': '3day',
    '3day':   'daily',
    'daily':  '4h',
    '4h':     '1h',
    '1h':     '30m',
    '30m':    '15m',
    '15m':    None,
}


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


# ── 顶级入口：周线 4 个 3 年段 ───────────────────────────────────────
TOP_RANGES = [
    {'label': '2017-08 ~ 2020-05', 'start': '17 Aug, 2017', 'end': '30 May, 2020'},
    {'label': '2020-03 ~ 2022-12', 'start': '17 Mar, 2020', 'end': '30 Dec, 2022'},
    {'label': '2022-10 ~ 2025-10', 'start': '17 Oct, 2022', 'end': '30 Oct, 2025'},
    {'label': '2025-04 ~ 2027-11', 'start': '17 Apr, 2025', 'end': '30 Nov, 2027'},
]


# ── 段数计算的核心常量 ───────────────────────────────────────────────
# 改它就可以全局控制"每段下一级图的目标根数"。
# 385 ≈ 一年多一点的日线窗口；改到 250 会切得更细，改到 500 段更粗。
BARS_PER_SEGMENT_TARGET = 385


# ── 主算法 ──────────────────────────────────────────────────────────
def has_drilldown(interval):
    """该周期是否还能继续钻取"""
    return NEXT_INTERVAL.get(interval) is not None


def next_interval(interval):
    """便捷别名"""
    return NEXT_INTERVAL.get(interval)


def interval_ratio(current, nxt):
    """current 周期 → nxt 周期的根数倍率（例 weekly→3day = 7/3）"""
    return INTERVAL_MINUTES[current] / INTERVAL_MINUTES[nxt]


def compute_segment_count(current_interval, current_bars):
    """
    给定"当前层 K 线根数"，计算钻到下一级时该把当前时间窗切成几段。

    公式：
        N_next = current_bars × ratio(current → next)
        段数   = floor(N_next / BARS_PER_SEGMENT_TARGET) + 1

    Returns
    -------
    int|None
        段数（≥1），或 None 表示当前周期已是末端，无法下钻。
    """
    nxt = NEXT_INTERVAL.get(current_interval)
    if nxt is None:
        return None
    n_next = current_bars * interval_ratio(current_interval, nxt)
    return int(n_next // BARS_PER_SEGMENT_TARGET) + 1


def compute_subranges(start_ts, end_ts, count):
    """
    把时间窗 [start_ts, end_ts] 平均切成 count 个子段。
    返回 list[(sub_start, sub_end)]，每个都是 pd.Timestamp。

    最后一段右端点对齐到 end_ts，避免浮点漂移。
    """
    if not isinstance(start_ts, pd.Timestamp):
        start_ts = pd.Timestamp(start_ts)
    if not isinstance(end_ts, pd.Timestamp):
        end_ts = pd.Timestamp(end_ts)

    if count < 1:
        raise ValueError(f"count must be >= 1, got {count}")

    total = end_ts - start_ts
    step  = total / count
    subs  = []
    for i in range(count):
        sub_start = start_ts + step * i
        sub_end   = start_ts + step * (i + 1) if i < count - 1 else end_ts
        subs.append((sub_start, sub_end))
    return subs


def format_range_label(start_ts, end_ts, interval):
    """时间窗格式化成 UI 可读标签。短周期带时分。"""
    if not isinstance(start_ts, pd.Timestamp):
        start_ts = pd.Timestamp(start_ts)
    if not isinstance(end_ts, pd.Timestamp):
        end_ts = pd.Timestamp(end_ts)
    fmt = '%m-%d %H:%M' if interval in ('15m', '30m', '1h', '4h') else '%Y-%m-%d'
    return f"{start_ts.strftime(fmt)} ~ {end_ts.strftime(fmt)}"


def to_binance_str(ts):
    """pd.Timestamp / datetime → Binance API 格式"""
    if not isinstance(ts, pd.Timestamp):
        ts = pd.Timestamp(ts)
    return ts.strftime('%d %b, %Y %H:%M:%S')
