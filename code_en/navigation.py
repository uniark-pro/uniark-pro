"""
Higher-interval projection onto lower intervals — drill-down navigation logic.
============================================================================
The core abstraction behind the "map → magnifying glass" workflow.

Workflow:
  1. User picks a symbol → picks one of the weekly time ranges (one of four
     three-year ranges) → views the weekly chart.
  2. Based on the K-line count, compute how many segments to slice the
     weekly window into; render each segment with the next-finer interval
     (3day) — this is "drilling".
  3. Repeat until reaching 15m (the bottom of the pyramid; no further
     subdivision).

Segment-count formula:
  N_next  = current_bars × (current_interval_minutes / next_interval_minutes)
  segments = floor(N_next / BARS_PER_SEGMENT_TARGET) + 1

Intuition: each slice at the next interval should hold approximately a
constant number of K-lines. BARS_PER_SEGMENT_TARGET is a soft target
governing "how many K-lines we want each chart to contain". 385 ≈ a bit
more than one year of daily candles.

Boundary cases:
  - segment count ≥ 1 (guaranteed by the formula). Even when only 1
    segment is computed, the UI still shows a button — the user must
    click to descend, preserving the consistent "view chart → pick
    segment → enter next level" interaction.
  - 15m is the terminus; it has no next level.
"""
import datetime as _dt
import pandas as pd


# ── Interval pyramid (top: weekly → terminus: 15m) ──────────────────
NEXT_INTERVAL = {
    'weekly': '3day',
    '3day':   'daily',
    'daily':  '4h',
    '4h':     '1h',
    '1h':     '30m',
    '30m':    '15m',
    '15m':    None,
}


# Minutes per single K-line at each interval. The interval ratio is
# derived from this — it's a physical fact, never assumed.
INTERVAL_MINUTES = {
    '15m':       15,
    '30m':       30,
    '1h':        60,
    '4h':        240,
    'daily':     1440,
    '3day':      4320,
    'weekly':    10080,
}


# ── Top-level entry: four 3-year weekly ranges ──────────────────────
TOP_RANGES = [
    {'label': '2017-08 ~ 2020-05', 'start': '17 Aug, 2017', 'end': '30 May, 2020'},
    {'label': '2020-03 ~ 2022-12', 'start': '17 Mar, 2020', 'end': '30 Dec, 2022'},
    {'label': '2022-10 ~ 2025-10', 'start': '17 Oct, 2022', 'end': '30 Oct, 2025'},
    {'label': '2025-04 ~ 2027-11', 'start': '17 Apr, 2025', 'end': '30 Nov, 2027'},
]


# ── Core constant for the segment-count formula ─────────────────────
# Tuning this controls the "target K-line count per next-level slice"
# globally. 385 ≈ a bit more than one year of daily candles; reducing to
# 250 yields finer slicing, raising to 500 yields coarser slicing.
BARS_PER_SEGMENT_TARGET = 385


# ── Main API ────────────────────────────────────────────────────────
def has_drilldown(interval):
    """Whether this interval can still be drilled further."""
    return NEXT_INTERVAL.get(interval) is not None


def next_interval(interval):
    """Convenience alias."""
    return NEXT_INTERVAL.get(interval)


def interval_ratio(current, nxt):
    """K-line-count ratio when going from `current` to `nxt`
    (e.g. weekly→3day = 7/3)."""
    return INTERVAL_MINUTES[current] / INTERVAL_MINUTES[nxt]


def compute_segment_count(current_interval, current_bars):
    """
    Given the K-line count at the current level, compute how many segments
    the current time window should be sliced into when drilling down to
    the next level.

    Formula:
        N_next   = current_bars × ratio(current → next)
        segments = floor(N_next / BARS_PER_SEGMENT_TARGET) + 1

    Returns
    -------
    int|None
        Segment count (≥1), or None if the current interval is already
        at the bottom of the pyramid and cannot drill further.
    """
    nxt = NEXT_INTERVAL.get(current_interval)
    if nxt is None:
        return None
    n_next = current_bars * interval_ratio(current_interval, nxt)
    return int(n_next // BARS_PER_SEGMENT_TARGET) + 1


def compute_subranges(start_ts, end_ts, count):
    """
    Slice the time window [start_ts, end_ts] evenly into `count` sub-ranges.
    Returns list[(sub_start, sub_end)], each a pd.Timestamp.

    The right end of the last sub-range is aligned to end_ts to avoid
    floating-point drift.
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
    """Format a time window as a UI-readable label.
    Short intervals include hour:minute."""
    if not isinstance(start_ts, pd.Timestamp):
        start_ts = pd.Timestamp(start_ts)
    if not isinstance(end_ts, pd.Timestamp):
        end_ts = pd.Timestamp(end_ts)
    fmt = '%m-%d %H:%M' if interval in ('15m', '30m', '1h', '4h') else '%Y-%m-%d'
    return f"{start_ts.strftime(fmt)} ~ {end_ts.strftime(fmt)}"


def to_binance_str(ts):
    """pd.Timestamp / datetime → Binance API format string."""
    if not isinstance(ts, pd.Timestamp):
        ts = pd.Timestamp(ts)
    return ts.strftime('%d %b, %Y %H:%M:%S')
