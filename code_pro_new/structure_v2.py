"""
公理化结构层 - 转折点与走势段
===============================
实现《交易体系的公理化构建》结构层：

定义【转折点】
    走势中将方向相反的连续段分隔开的价格点。
    - 红段（neg）→ 绿段（pos）：取两段合并K线范围内最低价的那根K线
    - 绿段（pos）→ 红段（neg）：取两段合并K线范围内最高价的那根K线

定义【一段走势】
    从一个转折点到下一个转折点之间的价格路径（用直线连接标识）。

噪声合并规则（min_seg_bars=7）
    bars < min_seg_bars 的段视为 S2，将 S1+S2+S3 整体合并：
    - 合并后符号取 S1（=S3）的符号
    - 面积只累加 S1+S3，S2 不计入
    - bars 为 S1+S2+S3 的总根数
    - 合并后若产生相邻同向段，继续归并
    重复直到稳定。

对外接口
--------
find_pivots(hist_series, low_series, high_series, min_seg_bars=7)
find_swings(hist_series, low_series, high_series, min_seg_bars=7)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass

from divergence import find_hist_segments


# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Pivot:
    """
    转折点

    bar_idx : K线下标
    price   : 转折价格（最低价 或 最高价）
    kind    : 'low'（红→绿的谷底） | 'high'（绿→红的峰顶）
    left_seg_idx  : 左侧段在合并后分段列表中的下标
    right_seg_idx : 右侧段在合并后分段列表中的下标
    """
    bar_idx: int
    price: float
    kind: str
    left_seg_idx: int
    right_seg_idx: int


@dataclass
class Swing:
    """
    一段走势（相邻两个转折点之间）

    direction : 'up'（从低点到高点） | 'down'（从高点到低点）
    start_pivot / end_pivot : 起止转折点
    """
    direction: str
    start_pivot: Pivot
    end_pivot: Pivot


# ─────────────────────────────────────────────────────────────────────────────
# 噪声合并
# ─────────────────────────────────────────────────────────────────────────────

def _merge_s1s2s3(segs: list[dict], min_seg_bars: int) -> list[dict]:
    """
    将 bars < min_seg_bars 的段视为 S2，把 S1+S2+S3 整体合并。

    合并规则：
    - 合并后符号取 S1（=S3）的符号
    - 面积只累加 S1+S3，S2 不计入
    - bars 为 S1+S2+S3 的总根数（start=S1.start, end=S3.end）
    - 合并后若产生相邻同向段，继续归并为一段（面积相加）
    重复直到无短段为止。
    """
    if min_seg_bars <= 0:
        return [dict(s) for s in segs]

    result = [dict(s) for s in segs]
    changed = True
    while changed:
        changed = False
        new = []
        i = 0
        while i < len(result):
            seg = result[i]
            # 当前段是短段（S2），且有左右两侧（S1, S3）
            if (seg['bars'] < min_seg_bars
                    and i > 0
                    and i < len(result) - 1):
                s1 = new[-1] if new else None
                s3 = result[i + 1]
                # S1 和 S3 必须反向于 S2（即 S1=S3 同向，S2 反向）
                if (s1 is not None
                        and s1['sign'] != seg['sign']
                        and s3['sign'] == s1['sign']):
                    # 合并 S1+S2+S3
                    merged = {
                        'sign':  s1['sign'],
                        'start': s1['start'],
                        'end':   s3['end'],
                        'area':  s1['area'] + s3['area'],   # 只加S1+S3
                        'bars':  s1['bars'] + seg['bars'] + s3['bars'],
                    }
                    new[-1] = merged   # 替换 S1
                    i += 2             # 跳过 S2 和 S3
                    changed = True
                    continue
            new.append(seg)
            i += 1

        # 归并相邻同向段
        merged_new = []
        for seg in new:
            if merged_new and merged_new[-1]['sign'] == seg['sign']:
                merged_new[-1]['end']   = seg['end']
                merged_new[-1]['area'] += seg['area']
                merged_new[-1]['bars'] += seg['bars']
                changed = True
            else:
                merged_new.append(seg)
        result = merged_new

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 核心实现
# ─────────────────────────────────────────────────────────────────────────────

def find_pivots(hist_series: pd.Series,
                low_series:  pd.Series,
                high_series: pd.Series,
                min_seg_bars: int = 7) -> list[Pivot]:
    """
    从MACD hist分段出发，识别全部转折点。

    规则：
    - 红（neg）→ 绿（pos）：合并两段K线范围，取最低价那根 → kind='low'
    - 绿（pos）→ 红（neg）：合并两段K线范围，取最高价那根 → kind='high'

    首尾边界不生成转折点，只保留相邻段交界处产生的转折点。

    min_seg_bars : 结构层噪声过滤阈值，与背离检测的 min_bars 完全独立。
    """
    raw_segs = find_hist_segments(hist_series)
    if not raw_segs:
        return []

    segs = _merge_s1s2s3(raw_segs, min_seg_bars)
    if not segs:
        return []

    low_vals  = low_series.values
    high_vals = high_series.values
    pivots: list[Pivot] = []

    # 转折点1：K线序列起点，无条件加入
    # kind 由第一段方向决定：pos段起点是低点，neg段起点是高点
    first_sign = segs[0]['sign']
    if first_sign == 'pos':
        pivots.append(Pivot(bar_idx=0, price=float(low_vals[0]),
                            kind='low', left_seg_idx=-1, right_seg_idx=0))
    else:
        pivots.append(Pivot(bar_idx=0, price=float(high_vals[0]),
                            kind='high', left_seg_idx=-1, right_seg_idx=0))

    for i in range(len(segs) - 1):
        left  = segs[i]
        right = segs[i + 1]
        merge_start = left['start']
        merge_end   = right['end']

        if left['sign'] == 'neg' and right['sign'] == 'pos':
            # 红→绿：取合并范围内最低价
            local_idx = int(np.nanargmin(low_vals[merge_start: merge_end + 1]))
            bar_idx = merge_start + local_idx
            pivots.append(Pivot(
                bar_idx=bar_idx,
                price=float(low_vals[bar_idx]),
                kind='low',
                left_seg_idx=i,
                right_seg_idx=i + 1,
            ))
        else:
            # 绿→红：取合并范围内最高价
            local_idx = int(np.nanargmax(high_vals[merge_start: merge_end + 1]))
            bar_idx = merge_start + local_idx
            pivots.append(Pivot(
                bar_idx=bar_idx,
                price=float(high_vals[bar_idx]),
                kind='high',
                left_seg_idx=i,
                right_seg_idx=i + 1,
            ))

    # ── 末端：最后一根K线无条件加为最后一个转折点 ───────────────────────
    last_idx = len(low_vals) - 1
    last_sign = segs[-1]['sign']
    if last_sign == 'pos':
        pivots.append(Pivot(bar_idx=last_idx, price=float(high_vals[last_idx]),
                            kind='high', left_seg_idx=len(segs)-1, right_seg_idx=-1))
    else:
        pivots.append(Pivot(bar_idx=last_idx, price=float(low_vals[last_idx]),
                            kind='low', left_seg_idx=len(segs)-1, right_seg_idx=-1))

    # ── 后处理：修正时间顺序不合理的转折点 ──────────────────────────────
    # 若 pivots[i].bar_idx >= pivots[i+1].bar_idx，说明顺序错乱。
    # 将 pivots[i] 限定在 pivots[i-1].bar_idx ~ pivots[i+1].bar_idx 之间重新找极值。
    # 重复直到序列严格递增。
    changed = True
    while changed:
        changed = False
        for i in range(1, len(pivots) - 1):
            prev_idx = pivots[i - 1].bar_idx
            curr_idx = pivots[i].bar_idx
            next_idx = pivots[i + 1].bar_idx
            if curr_idx <= prev_idx or curr_idx >= next_idx:
                # 在 (prev_idx, next_idx) 开区间内重新找极值
                lo = prev_idx + 1
                hi = next_idx - 1
                if lo > hi:
                    continue
                if pivots[i].kind == 'low':
                    local_idx = int(np.nanargmin(low_vals[lo: hi + 1]))
                    bar_idx = lo + local_idx
                    pivots[i] = Pivot(bar_idx=bar_idx,
                                      price=float(low_vals[bar_idx]),
                                      kind='low',
                                      left_seg_idx=pivots[i].left_seg_idx,
                                      right_seg_idx=pivots[i].right_seg_idx)
                else:
                    local_idx = int(np.nanargmax(high_vals[lo: hi + 1]))
                    bar_idx = lo + local_idx
                    pivots[i] = Pivot(bar_idx=bar_idx,
                                      price=float(high_vals[bar_idx]),
                                      kind='high',
                                      left_seg_idx=pivots[i].left_seg_idx,
                                      right_seg_idx=pivots[i].right_seg_idx)
                changed = True

    return pivots


def find_swings(hist_series: pd.Series,
                low_series:  pd.Series,
                high_series: pd.Series,
                min_seg_bars: int = 7) -> list[Swing]:
    """
    从转折点序列构建走势段序列。
    相邻两个转折点之间即为一段走势，方向由起止转折点的 kind 决定：
        low → high : 'up'
        high → low : 'down'
    """
    pivots = find_pivots(hist_series, low_series, high_series, min_seg_bars)
    swings: list[Swing] = []

    for i in range(len(pivots) - 1):
        p0 = pivots[i]
        p1 = pivots[i + 1]
        direction = 'up' if p0.kind == 'low' else 'down'
        swings.append(Swing(
            direction=direction,
            start_pivot=p0,
            end_pivot=p1,
        ))

    return swings
