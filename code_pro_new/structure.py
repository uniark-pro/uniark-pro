"""
structure.py — 结构层：转折点识别与走势段标注
===============================================

定义【转折点】
    走势中将方向相反的连续段分隔开的价格点。
    - 红段(neg)→绿段(pos)：取两段合并K线范围内最低价那根 → kind='low'
    - 绿段(pos)→红段(neg)：取两段合并K线范围内最高价那根 → kind='high'
    - K线起点无条件作为第一个转折点
    - K线末点无条件作为最后一个转折点

定义【一段走势】
    从一个转折点到下一个转折点之间的价格路径。

噪声过滤（STRUCTURE_MIN_SWING_BARS，存放于 config.py）
    走势段的K线根数 = 相邻转折点之间的根数（含两端）。
    不足该根数的走势段视为 S2，将 S1+S2+S3 合并为一个同向段：
    - 合并后方向取 S1（=S3）的方向
    - start_pivot 取 S1 的起点，end_pivot 取 S3 的终点
    重复直到稳定。

    注意：合并基于转折点层面的K线根数，不是 MACD 段的 bars。

对外接口
--------
find_pivots(hist_series, low_series, high_series)
    返回 list[Pivot]

annotate_pivots(price_ax, df, pivots)
    在价格面板上标注转折点圆点和编号
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass
from divergence import find_hist_segments
from config import STRUCTURE_MIN_SWING_BARS


# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Pivot:
    bar_idx : int    # K线下标
    price   : float  # 转折价格
    kind    : str    # 'low' | 'high'


# ─────────────────────────────────────────────────────────────────────────────
# 第一步：从原始 MACD 段找出转折点候选（不做任何合并）
# ─────────────────────────────────────────────────────────────────────────────

def _raw_pivots(segs: list[dict],
                low_vals: np.ndarray,
                high_vals: np.ndarray) -> list[Pivot]:
    """
    用原始 MACD 段（未合并）识别转折点候选序列。
    包含起点和末点。
    """
    pivots: list[Pivot] = []

    # 起点
    if segs[0]['sign'] == 'pos':
        pivots.append(Pivot(bar_idx=0, price=float(low_vals[0]),  kind='low'))
    else:
        pivots.append(Pivot(bar_idx=0, price=float(high_vals[0]), kind='high'))

    # 相邻段交界
    for i in range(len(segs) - 1):
        left  = segs[i]
        right = segs[i + 1]
        ms = left['start']
        me = right['end']
        if left['sign'] == 'neg' and right['sign'] == 'pos':
            local = int(np.nanargmin(low_vals[ms: me + 1]))
            bi = ms + local
            pivots.append(Pivot(bar_idx=bi,
                                price=float(low_vals[bi]),
                                kind='low'))
        else:
            local = int(np.nanargmax(high_vals[ms: me + 1]))
            bi = ms + local
            pivots.append(Pivot(bar_idx=bi,
                                price=float(high_vals[bi]),
                                kind='high'))

    # 末点
    last_idx = len(low_vals) - 1
    if segs[-1]['sign'] == 'pos':
        pivots.append(Pivot(bar_idx=last_idx,
                            price=float(high_vals[last_idx]),
                            kind='high'))
    else:
        pivots.append(Pivot(bar_idx=last_idx,
                            price=float(low_vals[last_idx]),
                            kind='low'))

    return pivots


# ─────────────────────────────────────────────────────────────────────────────
# 第二步：在转折点层面合并短走势段
# ─────────────────────────────────────────────────────────────────────────────

def _pivot_cluster_extra(pivot_bar: int,
                         direction: int,
                         low_vals: np.ndarray,
                         high_vals: np.ndarray) -> int:
    """
    计算转折点所在盘整簇向"段外方向"伸出的额外K线数。

    盘整结构判定：以转折点那根K线为 S2，前一根 S1、后一根 S3，
    三根存在重叠区间（max(lows) < min(highs)）则盘整成立。
    重叠区间确定后，向 direction 方向逐根检查：只要该K线 [low, high]
    与重叠区间有交集就并入盘整簇，计入额外K线数。

    参数
    ----
    pivot_bar : 转折点的K线下标
    direction : +1 向右扫描，-1 向左扫描
    返回向 direction 方向伸出走势段之外的额外K线数（不含转折点本身）。
    若该转折点不构成盘整结构，返回 0。
    """
    n = len(low_vals)
    i = pivot_bar
    if i - 1 < 0 or i + 1 >= n:
        return 0
    hi = min(high_vals[i - 1], high_vals[i], high_vals[i + 1])
    lo = max(low_vals[i - 1],  low_vals[i],  low_vals[i + 1])
    if hi <= lo:
        return 0
    extra = 0
    j = pivot_bar + direction
    while 0 <= j < n:
        if high_vals[j] > lo and low_vals[j] < hi:
            extra += 1
            j += direction
        else:
            break
    return extra


def _swing_bars(p_start: Pivot, p_end: Pivot,
                low_vals: np.ndarray,
                high_vals: np.ndarray) -> int:
    """
    一段走势 p_start→p_end 的K线根数。
    = 下标差(含两端) + 起点端盘整簇向左伸出 + 终点端盘整簇向右伸出
    """
    base = p_end.bar_idx - p_start.bar_idx + 1
    left_extra  = _pivot_cluster_extra(p_start.bar_idx, -1, low_vals, high_vals)
    right_extra = _pivot_cluster_extra(p_end.bar_idx,   +1, low_vals, high_vals)
    return base + left_extra + right_extra


def _merge_short_swings(pivots: list[Pivot],
                        low_vals: np.ndarray,
                        high_vals: np.ndarray,
                        min_bars: int) -> list[Pivot]:
    """
    走势段K线根数由 _swing_bars 计算（含两端转折点的盘整簇）。
    不足 min_bars 的段视为 S2，将 S1+S2+S3 合并：
    - S1 和 S3 同向（kind 相同），S2 反向
    - 合并后保留 S1 的起点，S3 的终点
    - 新转折点的 kind 由 S1/S3 的方向决定，价格重新从合并范围内取极值
    重复直到稳定。
    """
    if min_bars <= 0:
        return pivots

    result = list(pivots)
    changed = True
    while changed:
        changed = False
        new = []
        i = 0
        while i < len(result):
            p = result[i]
            # 检查以 p 为起点的走势段是否是短段（S2）
            if (i > 0                        # 有左侧段 S1
                    and i < len(result) - 1  # 有右侧段 S3
                    and len(new) > 0):
                # 当前走势段：new[-1] → p，根数（含盘整簇）
                s2_bars = _swing_bars(new[-1], p, low_vals, high_vals)
                if s2_bars < min_bars:
                    s1_pivot = new[-1]          # S1 的起点转折点
                    s3_pivot = result[i + 1]    # S3 的终点转折点
                    # S1 和 S3 必须同向（kind 相同），S2 反向
                    if s1_pivot.kind == s3_pivot.kind != p.kind:
                        # 合并：在 S1起点 到 S3终点 的范围内重新取极值
                        ms = s1_pivot.bar_idx
                        me = s3_pivot.bar_idx
                        # 若 S1 起点是第0根（数据起点），强制保留为转折点1，
                        # 不重新取极值（起点无条件是转折点1）
                        if ms == 0:
                            new[-1] = Pivot(bar_idx=0,
                                            price=s1_pivot.price,
                                            kind=s1_pivot.kind)
                        elif s1_pivot.kind == 'high':
                            local = int(np.nanargmax(high_vals[ms: me + 1]))
                            bi = ms + local
                            new[-1] = Pivot(bar_idx=bi,
                                            price=float(high_vals[bi]),
                                            kind='high')
                        else:
                            local = int(np.nanargmin(low_vals[ms: me + 1]))
                            bi = ms + local
                            new[-1] = Pivot(bar_idx=bi,
                                            price=float(low_vals[bi]),
                                            kind='low')
                        i += 2  # 跳过 S2（p）和 S3（result[i+1]）
                        changed = True
                        continue
            new.append(p)
            i += 1
        result = new

    # 归并相邻同向段：合并后可能出现相邻 kind 相同的转折点（中间反向段被合并掉），
    # 需要去掉多余的转折点。两个相邻同向 high：保留价格更高的；同向 low：保留更低的。
    # 起点（bar_idx==0）无条件保留位置和价格。
    changed = True
    while changed:
        changed = False
        merged = []
        for p in result:
            if merged and merged[-1].kind == p.kind:
                prev = merged[-1]
                if prev.bar_idx == 0:
                    # 起点无条件保留，丢弃后面的同向点
                    changed = True
                    continue
                # 取价格更极端的那个
                if p.kind == 'high':
                    merged[-1] = p if p.price >= prev.price else prev
                else:
                    merged[-1] = p if p.price <= prev.price else prev
                changed = True
            else:
                merged.append(p)
        result = merged

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 对外主接口
# ─────────────────────────────────────────────────────────────────────────────

def find_pivots(hist_series: pd.Series,
                low_series:  pd.Series,
                high_series: pd.Series,
                min_bars: int = STRUCTURE_MIN_SWING_BARS) -> list[Pivot]:
    """
    识别全部转折点。

    算法：
    1. 用原始 MACD 段（不合并）找转折点候选
    2. 在转折点层面，对走势段K线根数 < min_bars 的段做 S1+S2+S3 合并
    3. 修正时间顺序（单次线性扫描）
    """
    segs = find_hist_segments(hist_series)
    if not segs:
        return []

    low_vals  = low_series.values
    high_vals = high_series.values

    # 第一步：原始转折点候选
    pivots = _raw_pivots(segs, low_vals, high_vals)

    # 第二步：转折点层面合并短走势段
    # 起点已完成，参与合并（合并时保留最左段起点，起点不会丢失）。
    # 末点：若最后一段 hist 的 end 等于数据末尾下标，说明未翻号（走势未完成），
    # 末点不参与合并；否则末点参与合并（已完成的走势段可以被合并）。
    last_pivot  = pivots[-1]
    last_seg_end = segs[-1]['end']
    last_data_idx = len(low_vals) - 1
    last_seg_unfinished = (last_seg_end == last_data_idx)

    if last_seg_unfinished:
        # 末端未完成：末点不参与合并
        head = _merge_short_swings(pivots[:-1], low_vals, high_vals, min_bars)
        pivots = head + [last_pivot]
    else:
        # 末端已完成：全部参与合并
        pivots = _merge_short_swings(pivots, low_vals, high_vals, min_bars)

    # 第三步：修正时间顺序（单次线性扫描）
    for i in range(1, len(pivots) - 1):
        prev_idx = pivots[i - 1].bar_idx
        next_idx = pivots[i + 1].bar_idx
        curr_idx = pivots[i].bar_idx
        if curr_idx <= prev_idx or curr_idx >= next_idx:
            lo = prev_idx + 1
            hi = next_idx - 1
            if lo > hi:
                continue
            if pivots[i].kind == 'low':
                local = int(np.nanargmin(low_vals[lo: hi + 1]))
                bi = lo + local
                pivots[i] = Pivot(bar_idx=bi,
                                  price=float(low_vals[bi]),
                                  kind='low')
            else:
                local = int(np.nanargmax(high_vals[lo: hi + 1]))
                bi = lo + local
                pivots[i] = Pivot(bar_idx=bi,
                                  price=float(high_vals[bi]),
                                  kind='high')

    return pivots


# ─────────────────────────────────────────────────────────────────────────────
# 三段结构 · 重叠区间
# ─────────────────────────────────────────────────────────────────────────────

def seg_high(p_start: Pivot, p_end: Pivot) -> float:
    """一段走势的高点：kind='high' 那个转折点的价格"""
    if p_start.kind == 'high':
        return p_start.price
    return p_end.price

def seg_low(p_start: Pivot, p_end: Pivot) -> float:
    """一段走势的低点：kind='low' 那个转折点的价格"""
    if p_start.kind == 'low':
        return p_start.price
    return p_end.price

def find_first_consolidation(pivots: list[Pivot]):
    """
    从走势段序列中滑动扫描，找第一个存在重叠区间的三段结构。
    返回 (i, hi, lo) 或 None。
    """
    n = len(pivots)
    for i in range(n - 3):
        p0, p1, p2, p3 = pivots[i], pivots[i+1], pivots[i+2], pivots[i+3]
        hi = min(seg_high(p0, p1), seg_high(p1, p2), seg_high(p2, p3))
        lo = max(seg_low(p0, p1),  seg_low(p1, p2),  seg_low(p2, p3))
        if hi > lo:
            return i, hi, lo
    return None


def _seg_intersects_p(p_start: Pivot, p_end: Pivot,
                      p_lo: float, p_hi: float) -> bool:
    """判断一段走势是否与盘整区间 [p_lo, p_hi] 有价格交集"""
    sh = seg_high(p_start, p_end)
    sl = seg_low(p_start, p_end)
    return sh > p_lo and sl < p_hi


def _leaves_p(p_start: Pivot, p_end: Pivot,
               p_lo: float, p_hi: float) -> bool:
    """
    判断一段走势是否"从 P 区间内离开"：
    - 起点价格在区间内（含边界）
    - 终点价格超出区间（> hi 或 < lo）
    """
    start_in = p_lo <= p_start.price <= p_hi
    end_out  = p_end.price > p_hi or p_end.price < p_lo
    return start_in and end_out


def find_consolidations(pivots: list[Pivot]) -> list[dict]:
    """
    识别盘整序列 P1, P2, P3...

    算法：
    1. 从 index=1 开始滑动找第一个有重叠区间的三段 → P1
       盘整区间 [lo, hi] 固定为前三段的重叠区间（固定锚点）
    2. 从第四段开始逐段扫描 S(k)：
       - S(k) 与 P1 有价格交集 → P1 扩展到 S(k)，继续
       - S(k) 与 P1 无交集：
         检查 S(k)+S(k+1)+S(k+2) 是否构成新三段（有重叠区间且与P1无交集）
         成立 → P1 被破坏，P2 从 S(k) 开始
         不成立 → 继续扫描
    3. 对 P2 重复，识别 P3...
    """
    n = len(pivots)
    results = []

    scan_from = 1
    while scan_from <= n - 4:
        # 找第一个有重叠区间的三段作为 Pi 锚点
        found = None
        for i in range(scan_from, n - 3):
            p0,p1,p2,p3 = pivots[i],pivots[i+1],pivots[i+2],pivots[i+3]
            hi = min(seg_high(p0,p1), seg_high(p1,p2), seg_high(p2,p3))
            lo = max(seg_low(p0,p1),  seg_low(p1,p2),  seg_low(p2,p3))
            if hi > lo:
                found = (i, hi, lo)
                break
        if found is None:
            break

        pi_idx, p_hi, p_lo = found
        extend_end_idx = pi_idx + 3   # 至少扩展到第三段终点
        next_p_start = None

        # 从第四段起点开始逐段扫描
        k = pi_idx + 3
        while k < n - 1:
            sk_s = pivots[k]
            sk_e = pivots[k + 1]
            sh = seg_high(sk_s, sk_e)
            sl = seg_low(sk_s, sk_e)

            if sh > p_lo and sl < p_hi:
                # S(k) 与 P1 有价格交集 → P1 扩展
                extend_end_idx = k + 1
                k += 1
            else:
                # S(k) 与 P1 无交集
                # 检查 S(k)+S(k+1)+S(k+2) 是否构成有效新三段
                if k + 2 < n:
                    q0,q1,q2,q3 = pivots[k],pivots[k+1],pivots[k+2],pivots[k+3] if k+3 < n else pivots[k+2]
                    new_hi = min(seg_high(q0,q1), seg_high(q1,q2), seg_high(q2,q3))
                    new_lo = max(seg_low(q0,q1),  seg_low(q1,q2),  seg_low(q2,q3))
                    if new_hi > new_lo and (new_lo >= p_hi or new_hi <= p_lo):
                        # 新三段与 P1 无交集 → P1 被破坏，P2 从 S(k) 开始
                        next_p_start = k
                        break
                k += 1

        results.append({
            'start_idx':  pi_idx,
            'hi':         p_hi,
            'lo':         p_lo,
            'extend_idx': extend_end_idx,
        })

        if next_p_start is not None:
            scan_from = next_p_start
        else:
            break

    return results


# ─────────────────────────────────────────────────────────────────────────────
# 图表标注
# ─────────────────────────────────────────────────────────────────────────────

def annotate_pivots(price_ax, df: pd.DataFrame, pivots: list[Pivot]) -> None:
    """
    在价格面板上标注转折点圆点和编号（1, 2, 3...）。
    低点：绿色，编号在下方
    高点：红色，编号在上方
    批量 scatter 提升性能。
    """
    if not pivots:
        return

    low_pvs  = [(n, p) for n, p in enumerate(pivots, 1) if p.kind == 'low']
    high_pvs = [(n, p) for n, p in enumerate(pivots, 1) if p.kind == 'high']

    if low_pvs:
        price_ax.scatter([p.bar_idx for _, p in low_pvs],
                         [p.price   for _, p in low_pvs],
                         color='#00cc88', s=40, zorder=10,
                         marker='o', edgecolors='white', linewidths=0.5)
    if high_pvs:
        price_ax.scatter([p.bar_idx for _, p in high_pvs],
                         [p.price   for _, p in high_pvs],
                         color='#ff4455', s=40, zorder=10,
                         marker='o', edgecolors='white', linewidths=0.5)

    # 盘整区间：P1, P2, P3...
    import matplotlib.patches as _patches
    CON_COLORS = ['#ffaa00', '#aa88ff', '#00ccff', '#ff88aa', '#88ffaa']
    cons = find_consolidations(pivots)
    for n_con, con in enumerate(cons):
        color = CON_COLORS[n_con % len(CON_COLORS)]
        x0 = pivots[con['start_idx']].bar_idx
        x1 = pivots[con['extend_idx']].bar_idx
        hi = con['hi']
        lo = con['lo']
        rect = _patches.Rectangle(
            (x0, lo), x1 - x0, hi - lo,
            linewidth=1.2, edgecolor=color,
            facecolor=color + '15', zorder=8,
        )
        price_ax.add_patch(rect)
        price_ax.text(x1 + 0.5, (hi + lo) / 2,
                      'P' + str(n_con + 1) + '\n' + f'{lo:,.0f}-{hi:,.0f}',
                      color=color, fontsize=7, fontweight='bold',
                      ha='left', va='center', zorder=12)

    # 走势段连线 + S0,S1,S2... 标注
    import matplotlib.collections as _mc
    up_segs, dn_segs = [], []
    for i in range(len(pivots) - 1):
        p0, p1 = pivots[i], pivots[i + 1]
        seg = [(p0.bar_idx, p0.price), (p1.bar_idx, p1.price)]
        color = '#00cc88' if p0.kind == 'low' else '#ff4455'
        if p0.kind == 'low':
            up_segs.append(seg)
        else:
            dn_segs.append(seg)
        # 标注在连线中点
        mx = (p0.bar_idx + p1.bar_idx) / 2
        my = (p0.price + p1.price) / 2
        price_ax.text(mx, my, f'S{i}',
                      color=color, fontsize=6, fontweight='bold',
                      ha='center', va='center', zorder=11,
                      bbox=dict(boxstyle='round,pad=0.1', facecolor='white',
                                edgecolor=color, linewidth=0.5, alpha=0.7))
    if up_segs:
        price_ax.add_collection(_mc.LineCollection(
            up_segs, colors='#00cc88', linewidths=0.8, alpha=0.5, zorder=8))
    if dn_segs:
        price_ax.add_collection(_mc.LineCollection(
            dn_segs, colors='#ff4455', linewidths=0.8, alpha=0.5, zorder=8))

    for num, p in low_pvs:
        price_ax.text(p.bar_idx, p.price * 0.97, str(num),
                      color='#00cc88', fontsize=8, fontweight='bold',
                      ha='center', va='top', zorder=11)
    for num, p in high_pvs:
        price_ax.text(p.bar_idx, p.price * 1.03, str(num),
                      color='#ff4455', fontsize=8, fontweight='bold',
                      ha='center', va='bottom', zorder=11)
