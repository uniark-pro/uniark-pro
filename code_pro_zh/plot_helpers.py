"""
绘图辅助：把 divergence.py 检测到的背离结构标注到 MACD 面板上。

单一实现，供 plot_kline.py / app.py 复用。
任何与背离视觉表达相关的改动（颜色、形状、字号、布局）都只在本文件里改。

设计要点
--------
- 形状：底背离 = 上三角 ▲（暗示反转向上），顶背离 = 下三角 ▼（暗示反转向下）。
        箭头方向与"未来走势的预期方向"对齐。
- 布局：箭头紧贴 hist 极值柱（直观锚定触发位置），
        文字搬到 0 轴的对侧（hist 不会延伸的空旷区）。

        例（顶背离）：
              ▼               <- 箭头在绿柱上方
            (绿柱)
            ─0────────         <- 0 轴
            L2                 <- Lv 标识在 0 轴下方
            38%                <- 百分比再下一行

        例（底背离）：
            36%                <- 百分比
            L2                 <- Lv 标识
            ─0────────         <- 0 轴
            (红柱)
              ▲               <- 箭头在红柱下方

        这样：
        1. 同向背离的文字基线统一（顶背离全部对齐到 0 轴下方某一行，
           底背离全部对齐到 0 轴上方某一行），横向比较强度容易；
        2. 文字不再挤压 hist 柱周边，长期紧贴 hist 的视觉拥堵消失；
        3. 箭头依然指向真实触发位置，不影响读图直觉。

- 占位：水平宽度 ≈ 1 根 K 线宽。
- Lv1 时 Lv 行留空（不显示 "L1"），保持百分比对齐，密集触发时百分比依然
  在同一水平线上。

未完成 / 暂定信号（provisional）
--------------------------------
当 S_last 的右端点等于数据末尾，该段还可能继续延伸（hist 还没翻号），
ratio 只是当前快照而非判决。视觉上：
  - 箭头颜色不变（▲ 仍红 / ▼ 仍绿）—— 保留方向语义
  - Lv 标识和百分比改成 dodger blue + "?" 后缀 —— 警示数值不可靠
这样多空方向和"待确认"两种语义解耦，一眼能看清。

多尺度衰竭（same_terminal_l1）
------------------------------
当一条 Lv≥2 背离的末段位置上、Lv1 的面积比也独立通过（S_last/前一同向段
< 0.5）时，去重保留下的 Lv≥2 记录会带上 same_terminal_l1=True。这表示
力度衰竭在多个尺度上同时成立，是一个更强的信号。视觉上把单三角 ▲ / ▼
换成"双三角"——两个三角横向稍微错开叠加，肉眼可见双层效果。
"""

# 颜色：红=底背离（看涨反转），绿=顶背离（看跌反转）
from config import (
    COLOR_BULLISH, COLOR_BEARISH, COLOR_PROVISIONAL,
    MARKER_SIZE, MARKER_EDGE, LABEL_FONTSIZE,
    OFFSET_MARKER_PCT, DOUBLE_MARKER_DX,
    TEXT_FIRST_PCT, TEXT_SECOND_PCT,
    PROVISIONAL_SUFFIX,
)


def annotate_divergences(macd_ax, df, divergences):
    """
    在 MACD 面板上为每条 divergence 画一个紧凑的图标化标记。
    箭头紧贴 hist 极值柱，文字落在 0 轴对侧的空旷区。
    provisional=True 的信号文字用 dodger blue + "?" 后缀警示。

    若标记位置超出当前 ylim（常见于贴近 panel 上下沿的极端 hist 柱），
    绘完后会自动外扩 ylim 把所有标记纳入可视区，避免被裁。

    Parameters
    ----------
    macd_ax     : matplotlib.axes.Axes
                  MACD hist 所在的面板。
    df          : pandas.DataFrame
                  含 'hist' 列，下标必须与 divergence 中的 s3_start/s3_end 对齐
                  （即调用方传入的就是用于检测的同一份 df）。
    divergences : list[dict]
                  find_three_segment_divergences 的返回值。
    """
    if not divergences:
        return

    y_min, y_max = macd_ax.get_ylim()
    y_range = y_max - y_min
    off_marker      = y_range * OFFSET_MARKER_PCT
    text_lv_offset  = y_range * TEXT_FIRST_PCT      # Lv 行（近 0 轴）
    text_pct_offset = y_range * TEXT_SECOND_PCT     # 百分比行（远 0 轴）

    # 收集所有标记的 y 坐标，绘完后据此判断是否需要外扩 ylim
    marker_ys = []

    for div in divergences:
        s3s, s3e   = div['s3_start'], div['s3_end']
        ratio_pct  = div['ratio'] * 100
        level      = div['level']
        is_bullish = div['kind'] == 'bullish'
        # ── level 0：价格极值标识（标准三段背离漏掉、由反向 hist 段承载的
        #    顶/底极值）。只画一个三角锚在极值 K 线上，无 Lv / 百分比文字。
        if level == 0:
            pk = div.get('peak_idx', int((s3s + s3e) / 2))
            if is_bullish:
                y_marker = -off_marker          # ▲ 落在 0 轴下方
                macd_ax.scatter([pk], [y_marker], marker='^', s=MARKER_SIZE,
                                color=COLOR_BULLISH, edgecolors='white',
                                linewidths=MARKER_EDGE, zorder=5)
            else:
                y_marker = +off_marker          # ▼ 落在 0 轴上方
                macd_ax.scatter([pk], [y_marker], marker='v', s=MARKER_SIZE,
                                color=COLOR_BEARISH, edgecolors='white',
                                linewidths=MARKER_EDGE, zorder=5)
            marker_ys.append(y_marker)
            continue
        # provisional 用 .get 兼容老版 dict（可能没这字段）
        provisional = div.get('provisional', False)
        # 文字色：provisional 时切 dodger blue，否则跟箭头同色
        text_color  = COLOR_PROVISIONAL if provisional else (
            COLOR_BULLISH if is_bullish else COLOR_BEARISH
        )
        pct_suffix  = PROVISIONAL_SUFFIX if provisional else ''
        x_mid       = (s3s + s3e) / 2

        if is_bullish:
            # 底背离：箭头在 hist 红柱下方，文字搬到 0 轴上方
            extreme   = df['hist'].iloc[s3s:s3e + 1].min()   # 最深红柱（负值）
            marker    = '^'
            arrow_color = COLOR_BULLISH
            y_marker  = extreme - off_marker
            y_lv      = +text_lv_offset       # 0 轴上方第一行
            y_pct     = +text_pct_offset      # 0 轴上方第二行（更高）
            va_text   = 'center'
        else:
            # 顶背离：箭头在 hist 绿柱上方，文字搬到 0 轴下方
            extreme   = df['hist'].iloc[s3s:s3e + 1].max()   # 最高绿柱（正值）
            marker    = 'v'
            arrow_color = COLOR_BEARISH
            y_marker  = extreme + off_marker
            y_lv      = -text_lv_offset       # 0 轴下方第一行
            y_pct     = -text_pct_offset      # 0 轴下方第二行（更低）
            va_text   = 'center'

        # 收集本条标记的所有 y 坐标（箭头 / Lv 行 / 百分比行）
        marker_ys.extend([y_marker, y_lv, y_pct])

        # ── 箭头：紧贴 hist 极值柱（颜色永远反映方向，不被 provisional 影响）─
        # same_terminal_l1=True 时画双三角：两个三角横向错开一点叠加，
        # 形成肉眼可辨的双层效果，表示"力度衰竭在多个尺度上同时成立"。
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

        # ── Lv 行：0 轴对侧第一行（Lv2+ 才显示，Lv1 留空保持对齐）───────
        if level >= 2:
            macd_ax.text(
                x_mid, y_lv, f'L{level}',
                fontsize=LABEL_FONTSIZE, color=text_color,
                ha='center', va=va_text, fontweight='normal',
            )

        # ── 百分比行：0 轴对侧第二行（始终显示）─────────────────────────
        macd_ax.text(
            x_mid, y_pct, f'{ratio_pct:.0f}%{pct_suffix}',
            fontsize=LABEL_FONTSIZE, color=text_color,
            ha='center', va=va_text, fontweight='normal',
        )

    # ── ylim 自动外扩：把所有标记纳入可视区 ─────────────────────────
    # 只在标记真的越界时扩；扩多少 = 越界量 + 一点点 padding（panel 高度 2%），
    # 让箭头/文字不贴边。0 轴和 hist 柱位置保持原样。
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
    把每条背离的诊断信息打印到 stdout。

    与 annotate_divergences 是正交的——前者是图像，后者是文本日志。
    plot_kline.py 的 CLI 入口会用；app.py（web 服务）不需要。
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
