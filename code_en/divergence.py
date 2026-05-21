"""
Three-segment divergence detection module (S1 + S2 + S3, with hierarchical
recursive extension)
====================================================================

This is the single implementation of "three-segment structure /
same-direction momentum comparison / divergence judgment" within the
trading-system skeleton. All upper-layer applications (plot_single.py /
plot_3day.py / app.py / main.py and future extensions) should import
this module.

Axiomatic points
----------------
1. Slice the MACD histogram (hist) into consecutive segments based on
   sign (positive vs. negative).
2. Segments shorter than `min_bars` are treated as "noise" and merged
   into the adjacent opposite segment; adjacent same-sign segments are
   then coalesced. When min_bars=0 this step is a no-op.
3. On the merged segment sequence, scan three-segment windows of the
   form "same / opposite / same" to detect divergences.

Hierarchical extension
----------------------
The base structure P_1 = S1 + S2 + S3. Treat P_1 as a composite segment:
  - P_1.sign  = S1.sign (which equals S3.sign)
  - P_1.area  = S1.area + S3.area      (S2 is opposite, not counted)
  - P_1.span  spans from S1.start to S3.end

In longer sequences one can construct P_1 + S4 + S5 (where S4 is
opposite and S5 is same-direction), and apply the three-segment
divergence test again -> Level-2 divergence.
By induction: P_2 = P_1+S4+S5 extends to P_2+S6+S7 -> Level 3.

A Level-k structure occupies 2k+1 raw segments: k same-direction
segments alternating with (k-1) opposite segments, plus the trailing
same-direction segment -> 2k+1 segments total. When applying the
base three-segment test to "P + S(2k) + S(2k+1)", P is built from
the first 2k-1 segments.

Trigger conditions (identical across all levels):
  a. (rightmost same-direction segment).area / P_k.area < ratio_threshold
  b. bullish: rightmost same-direction segment low <
       min(lows of all same-direction segments inside P)
     bearish: rightmost same-direction segment high >
       max(highs of all same-direction segments inside P)

Opposite-barrier rule
---------------------
The axiom
~~~~~~~~~
"When does a structure start" is easier to define than "when does a
structure end":

  **A triggered opposite divergence = the starting point of the next
  same-direction structure.**

A same-direction structure D, during its formation from "start point ->
its own trigger point", is invalid if it spans across another
already-triggered opposite divergence D' — it would incorrectly merge
"pre-reversal" and "post-reversal" motion into the same P.

Under this axiomatic framework, there is no such thing as "a structure
being terminated by another structure". A same-direction motion either
keeps extending (in which case it is nothing yet), or triggers its own
divergence (becoming a completed D that then undergoes barrier
judgment). "When does extension end" is not a question for this layer.

Formalization
~~~~~~~~~~~~~
D is rejected iff there exists a surviving opposite D' satisfying:
  (a) D'.s3_end lies strictly within D's open interval
      (s1_start, s3_end)
      -- the executable form of "D crosses D's trigger point during
         its formation".
  (b) D' achieves a highest level > 1 at the same terminal position
      (kind, s3_start, s3_end)
      -- application-layer signal filtering, see below.

Condition (a) is the direct encoding of the axiom:
  - Anchor s3_end: the moment the opposite reversal is triggered;
    the natural definition of the next structure's starting point.
  - Open interval: when D'.s3_end == D.s1_start, D starts exactly
    after D' and does not cross it; when D'.s3_end == D.s3_end, both
    trigger simultaneously and geometrically there is no crossing.
  - Whole interval: D's formation spans from s1_start to s3_end,
    and any opposite trigger anywhere within counts as "crossing".

Condition (b): application-layer filtering on top of the axiom
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The axiom itself does not distinguish the level of D'. But in practice:
  - L1 opposite divergences are weak; they only produce a
    consolidation, not a true trend switch.
  - L1+L1 twin reversals (e.g. an S1+S2+S3 bullish-L1 immediately
    followed by an S2+S3+S4 bearish-L1) are the canonical "double
    reversal" pattern at market turns; geometrically each necessarily
    crosses the other's open interval. If the axiom applied
    unconditionally, the two would shield each other -- yet they
    should both be retained.

So condition (b) requires the barrier to be trend-level (L>=2): an L1
opposite trigger is not strong enough to invalidate a same-direction
structure crossing it. This is not a correction to the axiom; it is a
signal-filtering layer built on top of it.

Why "highest level at the same terminal position"
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
During structure extension the same terminal position
(kind, s3_start, s3_end) may simultaneously trigger L1 and L>=n.
Downstream _dedupe_same_terminal treats these as different
representations of the same divergence and keeps the one with the
highest level. Barrier judgment is consistent with this: the
"highest level reached at that position" decides barrier eligibility,
not the level of any specific record. That way, even if the
high-level candidate itself is shielded by another barrier, an L1
candidate surviving at the same position still wields barrier
strength according to that highest level; conversely, a pure L1 (no
higher-level candidate at the same position) still does not
constitute a barrier.

Resolution order
~~~~~~~~~~~~~~~~
D' may itself be rejected by an even more interior opposite
divergence, in which case D' is no longer an effective barrier.
Process candidates in ascending order of s3_end (earlier triggers
settle first): any D' capable of shielding D must satisfy
D'.s3_end < D.s3_end, so when an early-trigger candidate is being
processed, all possible interior barriers are already settled.
A single linear scan converges.

L1 is subject to barrier judgment (as the shielded party)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
L1 does not act as a barrier, but as the shielded party it still
participates in filtering. The single opposite segment S2 of an L1
can serve as the S_last of some L>=2 opposite divergence, whose
trigger point s3_end is exactly the end of L1's S2, which lies
strictly within L1's open interval (S1.start, S3.end). By the axiom,
L1's S1 and S3 belong to two different mechanisms (one before and
one after the trend switch) and should be shielded.

Note: condition (a) is strictly stronger than "opposite span fully
contained" -- if D'.span ⊆ D.span, then D'.s3_end necessarily lies
in (D.s1_start, D.s3_end]. When D' starts before D (the two overlap
but neither contains the other), condition (a) still captures it
correctly.

Public interface (still a single function):
    find_three_segment_divergences(hist, low, high,
                                   min_bars=0,
                                   ratio_threshold=0.5,
                                   max_level=1,
                                   block_by_opposite=True)

Parameter max_level:
    1 (default)  Detect only base three-segment (fully backward
                 compatible with historical behavior).
    2, 3, ...    Also detect extended levels.
    None         Exhaust all possible levels (until not enough
                 segments remain).

Parameter block_by_opposite:
    True  (default)  Apply the opposite-barrier rule
                     (user-preferred semantics).
    False            Skip filtering; return all raw candidates
                     (for debugging / reproducing legacy behavior).

Each returned record carries a new 'level' field indicating at which
level it was triggered (1 = base).
"""
import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
# Internal utility: raw segmentation
# ─────────────────────────────────────────────────────────────────────────────
def find_hist_segments(hist_series):
    """
    Slice the hist series into consecutive segments by sign.
    Returns list[dict], each dict:
        { 'sign': 'pos'|'neg', 'start': int, 'end': int,
          'area': float, 'bars': int }
    where start / end are closed-interval indices.
    """
    values = hist_series.values
    n = len(values)
    segments = []
    i = 0
    while i < n:
        v = values[i]
        if np.isnan(v):
            i += 1
            continue
        sign = 'neg' if v < 0 else 'pos'
        j = i
        while j < n and not np.isnan(values[j]) and (
            (values[j] < 0  and sign == 'neg') or
            (values[j] >= 0 and sign == 'pos')
        ):
            j += 1
        area = float(np.nansum(np.abs(values[i:j])))
        segments.append({
            'sign':  sign,
            'start': i,
            'end':   j - 1,
            'area':  area,
            'bars':  j - i,
        })
        i = j
    return segments


# ─────────────────────────────────────────────────────────────────────────────
# Internal utility: noise merging (no-op when min_bars=0)
# ─────────────────────────────────────────────────────────────────────────────
def _merge_short_segments(segs, noise_sign, host_sign, min_bars):
    """
    Merge segments of noise_sign shorter than min_bars into the adjacent
    host_sign segment. Prefer merging to the left; if the left is
    unavailable, merge to the right; after merging, adjacent same-sign
    segments are automatically coalesced. Repeat until stable.
    When min_bars<=0, `bars < min_bars` is never satisfied -> returns a
    deep copy immediately.
    """
    result = [dict(s) for s in segs]
    changed = True
    while changed:
        changed = False
        new_result = []
        skip = set()
        for i, seg in enumerate(result):
            if i in skip:
                continue
            if seg['sign'] == noise_sign and seg['bars'] < min_bars:
                left  = new_result[-1]   if new_result          else None
                right = result[i + 1]    if i + 1 < len(result) else None
                if left is not None and left['sign'] == host_sign:
                    left['end']   = seg['end']
                    left['area'] += seg['area']
                    left['bars'] += seg['bars']
                    changed = True
                elif right is not None and right['sign'] == host_sign:
                    rc = dict(right)
                    rc['start']  = seg['start']
                    rc['area']  += seg['area']
                    rc['bars']  += seg['bars']
                    new_result.append(rc)
                    skip.add(i + 1)
                    changed = True
                else:
                    new_result.append(seg)
            else:
                new_result.append(seg)
        result = new_result
        merged = []
        for seg in result:
            if merged and merged[-1]['sign'] == seg['sign']:
                merged[-1]['end']   = seg['end']
                merged[-1]['area'] += seg['area']
                merged[-1]['bars'] += seg['bars']
                changed = True
            else:
                merged.append(seg)
        result = merged
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Internal utility: hierarchical scanning
# ─────────────────────────────────────────────────────────────────────────────
def _scan_levels(segs, p_sign, low_series, high_series,
                 ratio_threshold, max_level, kind, min_bars):
    """
    Scan levels 1..max_level on segs (an alternating-sign sequence).

    A level-k structure spans 2k+1 segments:
      - Same-direction segments at offsets 0, 2, 4, ..., 2k-2 (k of them)
      - Opposite segments at offsets 1, 3, ..., 2k-1 (k of them)
      - The trailing same-direction segment at offset 2k
        (i.e. S_{2k+1} = "S5/S7/...", denoted S_last below)

    k=1: classical S1 + S2 + S3
    k=2: P_1(S1,S2,S3) + S4 + S5
    k=3: P_2(S1..S5) + S6 + S7
    """
    results = []
    if not segs:
        return results

    upper = max_level if max_level is not None else len(segs)

    for k in range(1, upper + 1):
        window = 2 * k + 1
        if window > len(segs):
            break

        for i in range(len(segs) - window + 1):
            # Check `window` segments starting at segs[i]; strict
            # alternation is required.
            block = segs[i:i + window]
            if block[0]['sign'] != p_sign:
                continue
            ok = True
            for j, s in enumerate(block):
                expected = p_sign if (j % 2 == 0) else (
                    'pos' if p_sign == 'neg' else 'neg'
                )
                if s['sign'] != expected:
                    ok = False
                    break
            if not ok:
                # After merging, adjacent same-sign segments cannot
                # appear; this explicit check is a safety net.
                continue

            same_sign_segs = [block[2 * j] for j in range(k)]   # 0,2,...,2k-2
            S_mid_last     = block[2 * k - 1]                   # second-to-last segment (opposite)
            S_last         = block[2 * k]                       # last segment (same-direction)

            # min_bars filter applies only at level 1 (base three-segment);
            # at higher levels P is a composite segment so bars are
            # necessarily large, and the intermediate opposite segments
            # are already guaranteed non-short by the merging step.
            if k == 1 and min(
                same_sign_segs[0]['bars'], S_mid_last['bars'], S_last['bars']
            ) < min_bars:
                continue

            # Area-ratio test: S_last.area / P.area
            P_area = sum(s['area'] for s in same_sign_segs)
            if P_area <= 0:
                continue
            ratio = S_last['area'] / P_area
            if ratio >= ratio_threshold:
                continue

            # New low / new high test
            if kind == 'bullish':
                p_low      = min(low_series.iloc[s['start']:s['end'] + 1].min()
                                 for s in same_sign_segs)
                s_last_low = low_series.iloc[S_last['start']:S_last['end'] + 1].min()
                if s_last_low >= p_low:
                    continue
            else:  # bearish
                p_high      = max(high_series.iloc[s['start']:s['end'] + 1].max()
                                  for s in same_sign_segs)
                s_last_high = high_series.iloc[S_last['start']:S_last['end'] + 1].max()
                if s_last_high <= p_high:
                    continue

            # Composite P's span / total bars (including intermediate
            # opposite segments)
            P_start = block[0]['start']
            P_end   = block[2 * k - 2]['end']   # tail of the third-from-last is P's tail
            P_bars  = sum(block[j]['bars'] for j in range(0, 2 * k - 1))

            results.append({
                'kind':     kind,
                'level':    k,
                # s1_* : P's span (at level=1 this equals S1)
                's1_start': P_start,
                's1_end':   P_end,
                # s3_* : last same-direction segment
                # (at level=1 this is S3; at level=2 this is S5)
                's3_start': S_last['start'],
                's3_end':   S_last['end'],
                's1_area':  P_area,
                's3_area':  S_last['area'],
                's1_bars':  P_bars,
                's2_bars':  S_mid_last['bars'],
                's3_bars':  S_last['bars'],
                'ratio':    ratio,
            })

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Internal utility: opposite-barrier filtering
# ─────────────────────────────────────────────────────────────────────────────
def _filter_by_opposite_barriers(divs):
    """
    Apply the opposite-barrier rule (the module-level docstring section
    "Opposite-barrier rule" is the axiomatic description; this function
    is its executable form).

    Axiom: a triggered opposite divergence = the starting point of the
    next same-direction structure. A same-direction D, if it crosses
    another already-triggered opposite D' during its formation, is
    rejected.

    Formalization: D is rejected iff there exists an opposite D' such
    that
      (a) D'.s3_end ∈ (D.s1_start, D.s3_end)
          (D crosses D's trigger point during formation)
      (b) D' reaches a highest level > 1 at the same terminal position
          (kind, s3_start, s3_end)
          (application-layer filter: an L1 opposite is not strong
          enough to invalidate a same-direction structure crossing it)

    Implementation: process candidates in ascending order of s3_end.
    Any opposite D' capable of shielding D must satisfy D'.s3_end <
    D.s3_end (it triggers earlier), so when processing an early-trigger
    candidate, all possible interior barriers are already settled.
    A single linear scan suffices.

    L1 still participates in filtering (as the shielded party)
    -----------------------------------------------------------
    Earlier versions unconditionally let level<2 candidates survive,
    on the reasoning that "a 3-segment r-g-r or g-r-g window's open
    interval contains only 1 opposite-color segment, which is too few
    to host an opposite divergence." That reasoning conflated
    "opposite divergence" with "the trigger point of an opposite
    divergence". The single opposite segment S2 of an L1 can perfectly
    serve as the S_last of some L>=2 opposite divergence; that
    divergence's trigger point s3_end is exactly the end of L1's S2,
    which lies strictly within L1's open interval (S1.start, S3.end).
    This is precisely the case the barrier rule is meant to catch:
    L1's S1 and S3 belong to two different mechanisms (one before and
    one after the trend switch) and must not be merged into a single P.

    L1 does NOT act as a barrier (rationale for condition b)
    --------------------------------------------------------
    Two adjacent same-level L1 opposite divergences (e.g. an S1+S2+S3
    bullish-L1 immediately followed by an S2+S3+S4 bearish-L1) form a
    canonical twin-reversal signal of a market turn and both should be
    retained. This geometric configuration is fully symmetric with
    "L1 shielded by L>=2" -- the only formal feature distinguishing the
    two is level. So condition (b) requires the barrier to be at least
    L>=2. This is not a correction to the axiom but a signal-filtering
    layer on top of it.

    Barrier strength judged by "highest level at the same terminal"
    --------------------------------------------------------------
    During structure extension, the same terminal position
    (kind, s3_start, s3_end) may simultaneously trigger L1 and L>=n
    divergences. Downstream _dedupe_same_terminal treats these
    candidates as different representations of the same divergence and
    flags only the one with the highest level. This function's barrier
    judgment is consistent with that: barrier eligibility is decided
    by "the highest level reached at that position in the raw
    candidate pool", not by the level of any specific record. That way,
    even if the high-level candidate is shielded by another barrier,
    the L1 candidate surviving at the same position still exercises
    barrier strength at the highest level; conversely, a pure L1 (no
    higher-level candidate at the same position) still does not
    constitute a barrier.
    """
    if not divs:
        return divs

    # Pre-compute, for each (kind, s3_start, s3_end) terminal position,
    # the highest level reached among the raw candidates. Barrier
    # strength is decided from this map, consistent with
    # _dedupe_same_terminal's semantics — multi-level candidates at the
    # same position are treated as different representations of the
    # same divergence, judged by the strongest one.
    max_level_at = {}
    for d in divs:
        key = (d['kind'], d['s3_start'], d['s3_end'])
        if d['level'] > max_level_at.get(key, 0):
            max_level_at[key] = d['level']

    # Sort by trigger point (s3_end) ascending; tie-break by level
    # ascending (only for stable ordering)
    sorted_divs = sorted(
        divs,
        key=lambda d: (d['s3_end'], d['level']),
    )

    survivors = []
    for d in sorted_divs:
        blocked = False
        for s in survivors:
            if s['kind'] == d['kind']:
                continue   # same direction does not constitute a barrier
            s_key = (s['kind'], s['s3_start'], s['s3_end'])
            if max_level_at.get(s_key, 0) <= 1:
                continue   # barrier's highest level at the same terminal is not >1, so it does not constitute a barrier
            # Does s's trigger point fall strictly inside d's open span?
            if d['s1_start'] < s['s3_end'] < d['s3_end']:
                blocked = True
                break

        if not blocked:
            survivors.append(d)

    return survivors


# ─────────────────────────────────────────────────────────────────────────────
# Internal utility: terminal deduplication (trend divergence > three-seg divergence)
# ─────────────────────────────────────────────────────────────────────────────
def _dedupe_same_terminal(divs):
    """
    Keep only the highest level at the same (kind, terminal) position.

    Background: the same S_last may simultaneously trigger divergences
    at multiple levels — e.g. when L2 fires, the trailing 3 segments
    can also form an L1 candidate (S1=L2.S3, S3=L2.S5). Since L2's
    price condition S5.low < min(S1.low, S3.low) is strictly stronger
    than that L1's S5.low < S3.low, the terminal L1's price condition
    is automatically satisfied. But the area ratio S_last/S3 < 0.5 is
    an independent condition that does not follow from L2's
    S_last/(S1+S3) < 0.5 — the prior segment can be small or large —
    so whether the terminal L1 holds independently must be checked via
    S_last/S3 alone.

    Semantically a trend divergence (L>=2) takes precedence over a
    three-segment divergence (L=1); visually we should not stack two
    percentages on the same K-line. So for the same kind and the same
    (s3_start, s3_end), we keep only the record with the largest level.

    The same_terminal_l1 flag
    -------------------------
    If the merged-away records include an L1 record (meaning the
    terminal L1 also holds independently — i.e. S_last / prior segment
    is also <0.5), the surviving record carries same_terminal_l1=True.
    The UI uses this field to render a double triangle, meaning
    "momentum exhaustion holds simultaneously at multiple scales — a
    stronger signal". If L>=2 fires but the terminal L1 does not hold
    (the prior segment is too small, so S_last / prior >0.5), then
    same_terminal_l1=False and the UI renders a single triangle.
    """
    by_key = {}
    has_l1 = {}        # key -> bool, whether any L1 record appeared at this key
    for d in divs:
        key = (d['kind'], d['s3_start'], d['s3_end'])
        if d['level'] == 1:
            has_l1[key] = True
        if key not in by_key or d['level'] > by_key[key]['level']:
            by_key[key] = d

    out = []
    for key, d in by_key.items():
        d = dict(d)   # avoid mutating the input
        # L1 itself does not count as "L1 also present" — this flag is
        # for L>=2 records that were retained while absorbing an L1
        d['same_terminal_l1'] = bool(has_l1.get(key, False)) and d['level'] >= 2
        out.append(d)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Public main function
# ─────────────────────────────────────────────────────────────────────────────
def find_three_segment_divergences(hist_series, low_series, high_series,
                                   min_bars=0, ratio_threshold=0.5,
                                   max_level=1, block_by_opposite=True):
    """
    Detect three-segment divergence structures on a MACD histogram
    (with hierarchical extension and opposite-barrier filtering).

    Parameters
    ----------
    hist_series       : pd.Series   MACD histogram (positive green, negative red)
    low_series        : pd.Series   K-line low prices
    high_series       : pd.Series   K-line high prices
    min_bars          : int         Minimum bars per segment (0 = no merge, no filter)
    ratio_threshold   : float       Area-ratio threshold, default 0.5
    max_level         : int|None    Hierarchical extension depth.
                                    1 = only base three-segment (default, backward compatible)
                                    2 = also detect P+S4+S5
                                    None = exhaust
    block_by_opposite : bool        Whether to apply the opposite-barrier
                                    rule (default True). A divergence is
                                    rejected if it spans an opposite D'
                                    that reaches a highest level >=2 at
                                    the same terminal position (a pure
                                    L1 opposite does not constitute a
                                    barrier). Set False to obtain the
                                    full unfiltered candidate set.

    Returns
    -------
    list[dict], sorted by (s3_start, level) ascending. Each record's fields:
        kind     : 'bullish' | 'bearish'
        level    : Triggering level (1 = base three-segment, 2 = P+S4+S5, etc.)
        s1_start : Start index of the left-side body (at level=1 this is S1.start)
        s1_end   : End index of the left-side body
        s3_start : Start index of the rightmost same-direction segment
        s3_end   : End index of the rightmost same-direction segment
        s1_area  : Area of the left-side body (sum of same-direction member areas)
        s3_area  : Area of the rightmost same-direction segment
        s1_bars  : Span of the left-side body in bars (including intermediate opposite segments)
        s2_bars  : Bars in the opposite segment immediately preceding S_last
        s3_bars  : Bars in the rightmost same-direction segment
        ratio    : s3_area / s1_area
        provisional : bool. True = S_last's right end equals the last
                     index of the data, meaning this segment may still
                     extend (future bars with the same sign will append;
                     only a sign flip "closes" it). The current ratio
                     and price-new-extremum are thus a snapshot, not a
                     verdict. The UI uses this field to switch colors
                     and warn the user. False = a sign flip has
                     occurred afterward, S_last is finalized, the
                     signal is confirmed.
        same_terminal_l1 : bool. Only records with level>=2 may be True.
                     Semantics: "the terminal position also supports an
                     independent L1" — i.e. S_last / immediately-prior
                     same-direction segment < 0.5 (note this ratio is
                     independent of the L>=2 ratio S_last /
                     sum-of-all-prior-same-direction-segments). Indicates
                     momentum exhaustion holds at multiple scales
                     simultaneously — a stronger signal. The UI uses
                     this field to render a double triangle. Records
                     with level=1 are always False.
    """
    raw_segs = find_hist_segments(hist_series)
    out = []

    # Bullish: P direction = neg
    segs_bull = _merge_short_segments(raw_segs, 'neg', 'pos', min_bars)
    out.extend(_scan_levels(segs_bull, 'neg', low_series, high_series,
                            ratio_threshold, max_level,
                            kind='bullish', min_bars=min_bars))

    # Bearish: P direction = pos
    segs_bear = _merge_short_segments(raw_segs, 'pos', 'neg', min_bars)
    out.extend(_scan_levels(segs_bear, 'pos', low_series, high_series,
                            ratio_threshold, max_level,
                            kind='bearish', min_bars=min_bars))

    # Mark "unfinished / provisional":
    # When S_last's right end equals the last index of the hist series,
    # this segment may still extend (future bars with the same sign
    # will append; only a sign flip "closes" it). The current ratio is
    # just a snapshot, not a verdict. The UI uses this field to switch
    # colors (bright yellow + "?" suffix) to warn the user.
    last_index = len(hist_series) - 1
    for d in out:
        d['provisional'] = (d['s3_end'] == last_index)

    # Opposite-barrier filtering
    if block_by_opposite:
        out = _filter_by_opposite_barriers(out)

    # Terminal deduplication: for the same kind and (s3_start, s3_end),
    # keep only the highest level. A trend divergence (L>=2) takes
    # precedence over a three-segment divergence (L=1). Deduplication
    # runs AFTER barrier filtering — this way, if an L>=2 candidate is
    # rejected by a barrier, the L1 at the same position can still
    # survive. During deduplication, if an L1 also holds independently
    # at the same position, the retained record is flagged with
    # same_terminal_l1=True, and the UI renders a double triangle
    # (momentum exhaustion at multiple scales simultaneously).
    out = _dedupe_same_terminal(out)

    out.sort(key=lambda d: (d['s3_start'], d['level']))
    return out
