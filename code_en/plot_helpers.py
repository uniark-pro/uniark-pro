"""
Plotting helpers: render the divergence structures detected by
divergence.py onto the MACD panel.

Single canonical implementation, reused by plot_kline.py / app.py.
Any change to the visual presentation of divergences (color, shape,
font size, layout) should be made here only.

Design notes
------------
- Shape: bullish divergence → upward triangle ▲ (suggesting an upward
         reversal); bearish divergence → downward triangle ▼
         (suggesting a downward reversal). The arrow direction aligns
         with the *expected future direction* of price.
- Layout: the arrow is anchored to the hist extremum bar (intuitively
          marking the trigger position); the text labels are moved to
          the OPPOSITE side of the zero axis (the empty region the
          hist does not extend into).

        Example (bearish):
              ▼               <- arrow above the green bar
            (green bar)
            ─0────────         <- zero axis
            L2                 <- level label below the zero axis
            38%                <- percentage on the next line below

        Example (bullish):
            36%                <- percentage
            L2                 <- level label
            ─0────────         <- zero axis
            (red bar)
              ▲               <- arrow below the red bar

        Benefits:
        1. Same-direction divergence labels share a baseline (all
           bearish percentages on one row below the zero axis;
           all bullish ones on one row above) — easy lateral
           strength comparison.
        2. Text no longer crowds the area around hist bars; the
           visual congestion that used to plague that region is gone.
        3. Arrows still point to the actual trigger position,
           preserving chart-reading intuition.

- Footprint: horizontal width ≈ one K-line wide.
- For Lv1, the level row is left blank (we do NOT print "L1") —
  this keeps percentages aligned: when divergences trigger densely,
  all percentages still live on the same horizontal line.

Provisional / unsettled signals
-------------------------------
When S_last's right end equals the last index of the data, that
segment may still extend (hist has not yet flipped sign). The ratio
is just a current snapshot, not a verdict. Visually:
  - Arrow color is unchanged (▲ stays red, ▼ stays green) —
    preserving the directional semantics.
  - Level label and percentage switch to dodger blue + "?" suffix —
    warning that the numbers are unreliable.
This decouples the two semantics — direction (reliable) and
"awaiting confirmation" (uncertain) — making them visually distinct.

Multi-scale decay (same_terminal_l1)
------------------------------------
When an Lv≥2 divergence's terminal position also independently
satisfies the Lv1 area ratio (S_last / prev co-directional segment
< 0.5), the surviving Lv≥2 record after dedupe carries
same_terminal_l1=True. This means force decay holds simultaneously
at multiple scales — a stronger signal. Visually we replace the
single triangle ▲ / ▼ with a "double triangle": two triangles
slightly offset horizontally and overlapping, producing a visibly
two-layer effect.
"""

# Colors: red = bullish (upward reversal), green = bearish (downward reversal)
COLOR_BULLISH = '#ff3344'
COLOR_BEARISH = '#22aa44'

# Provisional-warning color: dodger blue. Bright, clearly distinct from
# the cyan tones of MA99 and DIF, and reads better on a white panel
# than the previous bright yellow.
COLOR_PROVISIONAL = '#1e90ff'

# Visual parameters (tweak here to compress / enlarge)
MARKER_SIZE       = 80      # scatter size of the triangle marker
MARKER_EDGE       = 0.8     # white outline width (lifts the arrow off the hist bar)
LABEL_FONTSIZE    = 9

# Offset parameters (percentages of MACD-panel height)
OFFSET_MARKER_PCT = 0.05    # arrow distance from the hist extremum bar

# Horizontal offset of the double triangle (data coords = K-line bars).
# Two triangles are drawn at x_mid ± this offset, producing a layered
# visual. 0.8 ≈ one K-line wide; once offset, both triangles are
# clearly distinguishable and don't occlude each other.
DOUBLE_MARKER_DX = 0.8

# Text labels are moved to the opposite side of the zero axis, with two
# rows extending outward from zero:
TEXT_FIRST_PCT    = 0.08    # first row (closer to zero axis) — distance from zero
TEXT_SECOND_PCT   = 0.15    # second row — distance from zero
# Row convention (regardless of bullish / bearish):
#   "row closer to zero axis" = level label (Lv1 left blank)
#   "row farther from zero axis" = percentage

# Percentage suffix for provisional signals
PROVISIONAL_SUFFIX = ' ?'


def annotate_divergences(macd_ax, df, divergences):
    """
    For each divergence, draw a compact icon-style marker on the MACD
    panel. The arrow is anchored to the hist extremum bar; the text
    sits on the empty side of the zero axis. Provisional signals
    (provisional=True) are flagged with dodger-blue text + "?" suffix.

    If a marker position falls outside the current ylim (common for
    extreme hist bars near the panel's top/bottom edges), the ylim
    is automatically extended after drawing so all markers are
    visible — preventing clipping.

    Parameters
    ----------
    macd_ax     : matplotlib.axes.Axes
                  The panel hosting the MACD histogram.
    df          : pandas.DataFrame
                  Must contain a 'hist' column; its index must align
                  with the s3_start / s3_end values in `divergences`
                  (i.e. the same df used for detection).
    divergences : list[dict]
                  Return value of find_three_segment_divergences.
    """
    if not divergences:
        return

    y_min, y_max = macd_ax.get_ylim()
    y_range = y_max - y_min
    off_marker      = y_range * OFFSET_MARKER_PCT
    text_lv_offset  = y_range * TEXT_FIRST_PCT      # level row (closer to zero)
    text_pct_offset = y_range * TEXT_SECOND_PCT     # percentage row (farther from zero)

    # Collect y-coordinates of all markers; after drawing, decide
    # whether to extend ylim.
    marker_ys = []

    for div in divergences:
        s3s, s3e   = div['s3_start'], div['s3_end']
        ratio_pct  = div['ratio'] * 100
        level      = div['level']
        is_bullish = div['kind'] == 'bullish'
        # Use .get for backward compat with old dict shapes that may
        # not include this field.
        provisional = div.get('provisional', False)
        # Text color: dodger blue when provisional, otherwise matches
        # the arrow color.
        text_color  = COLOR_PROVISIONAL if provisional else (
            COLOR_BULLISH if is_bullish else COLOR_BEARISH
        )
        pct_suffix  = PROVISIONAL_SUFFIX if provisional else ''
        x_mid       = (s3s + s3e) / 2

        if is_bullish:
            # Bullish: arrow below the deepest red bar; text moved
            # above the zero axis.
            extreme   = df['hist'].iloc[s3s:s3e + 1].min()   # deepest red bar (negative)
            marker    = '^'
            arrow_color = COLOR_BULLISH
            y_marker  = extreme - off_marker
            y_lv      = +text_lv_offset       # first row above zero
            y_pct     = +text_pct_offset      # second row above zero (higher)
            va_text   = 'center'
        else:
            # Bearish: arrow above the tallest green bar; text moved
            # below the zero axis.
            extreme   = df['hist'].iloc[s3s:s3e + 1].max()   # tallest green bar (positive)
            marker    = 'v'
            arrow_color = COLOR_BEARISH
            y_marker  = extreme + off_marker
            y_lv      = -text_lv_offset       # first row below zero
            y_pct     = -text_pct_offset      # second row below zero (lower)
            va_text   = 'center'

        # Collect this record's marker y-coords (arrow / level row /
        # percentage row).
        marker_ys.extend([y_marker, y_lv, y_pct])

        # ── Arrow: anchored to the hist extremum bar (color always
        #    reflects direction, not affected by provisional) ───────
        # When same_terminal_l1=True, draw a double triangle: two
        # triangles slightly offset horizontally and overlapping,
        # producing a clearly two-layer effect — meaning "force decay
        # holds simultaneously at multiple scales".
        same_terminal_l1 = div.get('same_terminal_l1', False)
        if same_terminal_l1:
            xs = [x_mid - DOUBLE_MARKER_DX, x_mid + DOUBLE_MARKER_DX]
            ys = [y_marker, y_marker]
        else:
            xs = [x_mid]
            ys = [y_marker]
        macd_ax.scatter(
            xs, ys,
            marker=marker, s=MARKER_SIZE,
            color=arrow_color, edgecolors='white', linewidths=MARKER_EDGE,
            zorder=5,
        )

        # ── Level row: first row on the opposite side of the zero
        #    axis (only Lv2+ shown; Lv1 left blank to keep alignment)
        if level >= 2:
            macd_ax.text(
                x_mid, y_lv, f'L{level}',
                fontsize=LABEL_FONTSIZE, color=text_color,
                ha='center', va=va_text, fontweight='normal',
            )

        # ── Percentage row: second row on the opposite side of the
        #    zero axis (always shown).
        macd_ax.text(
            x_mid, y_pct, f'{ratio_pct:.0f}%{pct_suffix}',
            fontsize=LABEL_FONTSIZE, color=text_color,
            ha='center', va=va_text, fontweight='normal',
        )

    # ── Auto-extend ylim to include all markers ─────────────────────
    # Only extend when markers actually overflow; the amount = overflow
    # + a small padding (2% of panel height) so arrows/text don't
    # touch the edge. Zero axis and hist bars stay in place.
    if marker_ys:
        needed_min = min(marker_ys)
        needed_max = max(marker_ys)
        pad        = y_range * 0.02
        new_min    = min(y_min, needed_min - pad)
        new_max    = max(y_max, needed_max + pad)
        if new_min < y_min or new_max > y_max:
            macd_ax.set_ylim(new_min, new_max)


def print_divergences(df, divergences):
    """
    Print diagnostic information for each divergence to stdout.

    Orthogonal to annotate_divergences — that's the visual side, this
    is the textual log. plot_kline.py's CLI entry point uses this;
    app.py (web service) does not.
    """
    if not divergences:
        print("No divergences detected.")
        return

    for div in divergences:
        kind_str = 'Bullish' if div['kind'] == 'bullish' else 'Bearish'
        s3s, s3e = div['s3_start'], div['s3_end']
        prov_tag = ' [provisional]' if div.get('provisional', False) else ''
        l1_tag   = ' [+L1]' if div.get('same_terminal_l1', False) else ''
        print(
            f"[{kind_str} Div. Lv{div['level']}] "
            f"S3/P={div['ratio'] * 100:.1f}% "
            f"S3:{df.index[s3s].strftime('%Y-%m-%d')}~"
            f"{df.index[s3e].strftime('%Y-%m-%d')} "
            f"P={div['s1_area']:.0f}({div['s1_bars']}b) "
            f"S3={div['s3_area']:.0f}({div['s3_bars']}b)"
            f"{prov_tag}{l1_tag}"
        )
