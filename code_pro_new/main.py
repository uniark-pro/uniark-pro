"""
K线分析 - 桌面 UI(导航栈版 + 背离钻取 + 设置 + 多 market)
============================================================
工作流:
  1. 选 market(虚拟币/美股/A股/港股)→ 选标的 → 选入口周期 → 选时段 → 点 "Generate Chart"
  2. subprocess 生成 PNG,系统看图器打开;UI 切到导航视图
  3. 从 plot_kline.py 的 stdout 解析 BARS=N 和 DIVS_JSON=...(本图检出的所有背离信号)
  4. 把每条背离做成可点击卡片(底背离红/顶背离绿,带 level 和 ratio)
  5. 点卡片 = 选定该背离的极值时间作"锁定锚点",钻到下一级周期窗口
     (peak ± 185/100 根次级 K 线时长,可按父级 S3 投影自适应扩展);
     次级别图的主面板 MA99 在锚点 S_last
     时间窗内染成红色,视觉上把锚点"传"到次级别。
  6. 锁定后,下一级图变成"单按钮"模式——继续钻取仍使用同一锚点,
     直到金字塔末端(crypto 15m / stock 1h)或用户面包屑回退。

入口周期(按 market 不同):
  - crypto                          : weekly / 3day / daily
  - us_stock / cn_stock / hk_stock  : weekly / daily
  其余周期(4h、1h 等)只能通过钻取访问,不在主界面顶级出现。

设置:
  顶栏 ⚙ 按钮打开设置对话框,可改语言、当前 market 的标的列表、
  当前 market 各入口周期的时段列表。改动写入 user_settings.json,
  下次启动自动加载。app.py 共享同一份设置。
"""
import tkinter as tk
import subprocess
import sys
import os
import re
import datetime as _dt

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)

import json
from navigation import (next_interval as nav_next_interval,
                        compute_divergence_drill_window,
                        INTERVAL_MINUTES)
import settings
from settings import MARKETS, ENTRY_INTERVALS_BY_MARKET, get_entry_intervals
from settings_dialog_tk import open_settings_dialog
from plot_kline import _resolve_symbol_config


# ── 配色 ──────────────────────────────────────────────────
BG        = "#1e1e2e"
BG_CARD   = "#2a2a3e"
ACCENT    = "#7c6af7"
ACCENT2   = "#f7a26a"
FG        = "#e0e0f0"
FG_DIM    = "#8888aa"
SEL_BG    = "#3a3a5e"
BTN_BG    = "#7c6af7"
BTN_FG    = "#ffffff"
BTN_HOVER = "#9b8dff"
BTN2_BG   = "#44446a"
BTN2_HOVER= "#5a5a7a"
OK_COLOR  = "#50fa7b"

# 背离卡片配色
DIV_BULLISH_FG = "#ff5566"   # 底背离卡片头色(红=反转向上)
DIV_BEARISH_FG = "#22cc44"   # 顶背离卡片头色(绿=反转向下)
DIV_PROVISIONAL_FG = "#1e90ff"  # 未完成信号
DIV_BORDER = "#f7a26a"        # 锁定模式卡片边框色(跟 ACCENT2 同源,提示"锚定")
DIV_TITLE_FG = "#f7a26a"      # 锁定模式标题色
ERR_COLOR = "#ff5555"
LANG_BG   = "#2a2a3e"
LANG_SEL  = "#3a3a5e"
BORDER    = "#44446a"

FONT_TITLE  = ("Noto Sans", 11, "bold")
FONT_OPTION = ("Noto Sans", 11)
FONT_BTN    = ("Noto Sans", 11, "bold")
FONT_STATUS = ("Noto Sans", 9)
FONT_LANG   = ("Noto Sans", 10, "bold")
FONT_CRUMB  = ("Noto Sans", 9)
FONT_SUB    = ("Noto Sans", 10)
FONT_SUB_S  = ("Noto Sans", 8)


# ── 多语言 ────────────────────────────────────────────────
I18N = {
    'en': {
        'title':     "K-Line",
        'market':    "▸  Market",
        'symbol':    "▸  Symbol",
        'interval':  "▸  Interval",
        'timerange': "▸  Time Range",
        'show':      "Generate Chart",
        'drill':     "▸  Click a divergence to drill into next level",
        'reset':     "↺  Start over",
        'generating':"Generating",
        'opens':     "Opening",
        'done':      "Done ✓",
        'error':     "Error ✗",
        'end_msg':   "(End of pyramid — no further drill-down available)",
        'mk_crypto':   "Crypto",
        'mk_us_stock': "US Stocks",
        'mk_cn_stock': "A-Shares",
        'mk_hk_stock': "HK Stocks",
        'iv_15m':    "15m",  'iv_30m':"30m", 'iv_1h':"1h", 'iv_4h':"4h",
        'iv_daily':  "Daily",'iv_3day':"3-Day", 'iv_weekly':"Weekly",
        'no_symbols':"No symbols configured. Click ⚙ to add one.",
        'no_ranges': "No time ranges configured for this interval. Click ⚙ to add one.",
        'div_drill': "▸  Click a divergence to drill into next level",
        'div_locked_head': "🔒  Locked anchor — continue drilling",
        'div_none':  "(No divergence detected in current view)",
        'div_bullish': "Bullish",
        'div_bearish': "Bearish",
        'div_extreme': "Extreme",
        'div_provisional': "Provisional — not yet closed, drill-down disabled",
    },
    'zh': {
        'title':     "K线",
        'market':    "▸  市场",
        'symbol':    "▸  标的",
        'interval':  "▸  周期",
        'timerange': "▸  时间段",
        'show':      "生成图表",
        'drill':     "▸  点击背离卡片钻取到下一级",
        'reset':     "↺  重新开始",
        'generating':"正在生成",
        'opens':     "正在打开",
        'done':      "完成 ✓",
        'error':     "错误 ✗",
        'end_msg':   "(已到金字塔末端 — 下方无更细周期)",
        'mk_crypto':   "虚拟币",
        'mk_us_stock': "美股",
        'mk_cn_stock': "A股",
        'mk_hk_stock': "港股",
        'iv_15m':    "15分钟", 'iv_30m':"30分钟", 'iv_1h':"1小时", 'iv_4h':"4小时",
        'iv_daily':  "日线", 'iv_3day':"3日线", 'iv_weekly':"周线",
        'no_symbols':"未配置标的，点击 ⚙ 添加。",
        'no_ranges': "该周期未配置时间段，点击 ⚙ 添加。",
        'div_drill': "▸  点击下方背离卡片,钻取到次级别",
        'div_locked_head': "🔒  锁定锚点钻取链 — 继续钻取下一级",
        'div_none':  "(当前视图未检测到背离信号)",
        'div_bullish': "底背离",
        'div_bearish': "顶背离",
        'div_extreme': "极值",
        'div_provisional': "未完成 · 尚未封口,暂不可钻取",
    }
}


# ── 工具 ───────────────────────────────────────────────────
def to_binance_str(dt):
    return dt.strftime('%d %b, %Y %H:%M:%S')

def parse_binance_str(s):
    return _dt.datetime.strptime(s, '%d %b, %Y')

def fmt_range(s, e, interval):
    if interval in ('15m', '30m', '1h', '4h'):
        f = lambda d: d.strftime('%m-%d %H:%M')
    else:
        f = lambda d: d.strftime('%Y-%m-%d')
    return f"{f(s)} ~ {f(e)}"


# ── 状态（从 settings 加载，可在运行时被设置对话框改写）─────
# 双 market 状态：每个 market 一份独立的 symbols / ranges / 选中下标。
# 切换 market 只是切换"当前活跃的视图层"，另一个 market 的状态保持不变。
_initial = settings.load_settings()
lang     = _initial['language']
sel_market = _initial.get('market', 'crypto')

# market_state[market] = {
#   'symbols':       list[str]            ← ticker 字符串数组（业务层用）
#   'cn_name_map':   dict[ticker -> str]  ← 渲染按钮时查中文名用
#   'ranges_all':    {interval: list[range]},
#   'sel_symbol':    str|None,
#   'sel_interval':  str,
#   'sel_rng_idx':   {interval: int},
# }
market_state = {}
for mk in MARKETS:
    block = _initial.get(mk, {})
    sym_list = block.get('symbols', [])      # v4 dict 数组
    syms = settings.extract_tickers(sym_list)
    name_map = settings.extract_name_map(sym_list)
    ivs = get_entry_intervals(mk)
    rngs_all = {iv: list(block.get('ranges', {}).get(iv, [])) for iv in ivs}
    market_state[mk] = {
        'symbols':      syms,
        'cn_name_map':  name_map,
        'ranges_all':   rngs_all,
        'sel_symbol':   syms[0] if syms else None,
        'sel_interval': ivs[0],
        # 默认选最后一项（最新时段）
        'sel_rng_idx':  {iv: len(rngs_all[iv]) - 1 if rngs_all[iv] else 0
                         for iv in ivs},
    }

nav_stack = []


# ── 当前 market 的状态访问器（薄封装）───────────────────────
def cur_state():
    return market_state[sel_market]

def current_symbols():
    return cur_state()['symbols']

def current_entry_intervals():
    return get_entry_intervals(sel_market)

def current_interval():
    return cur_state()['sel_interval']

def current_symbol():
    return cur_state()['sel_symbol']

def current_ranges():
    return cur_state()['ranges_all'].get(current_interval(), [])

def current_rng_idx():
    return cur_state()['sel_rng_idx'].get(current_interval(), 0)


# ── 主窗口 ────────────────────────────────────────────────
root = tk.Tk()
root.configure(bg=BG)
root.geometry("680x880")
root.resizable(False, False)


def t(key):
    return I18N[lang][key]


# ── 顶部栏 ────────────────────────────────────────────────
top_bar = tk.Frame(root, bg=BG)
top_bar.pack(fill="x", padx=20, pady=(12, 0))

title_lbl = tk.Label(top_bar, text="", font=("Noto Sans", 14, "bold"),
                     bg=BG, fg=ACCENT)
title_lbl.pack(side="left")

# 顶栏右侧：⚙ 设置按钮 + 语言切换
top_right = tk.Frame(top_bar, bg=BG)
top_right.pack(side="right")

settings_btn = tk.Label(top_right, text="⚙", font=("Noto Sans", 14),
                        bg=BG_CARD, fg=FG, padx=10, pady=2, cursor="hand2",
                        highlightthickness=1, highlightbackground=BORDER)
settings_btn.pack(side="left", padx=(0, 8))

lang_frame = tk.Frame(top_right, bg=BG_CARD, highlightthickness=1,
                      highlightbackground=BORDER)
lang_frame.pack(side="left")
btn_en = tk.Label(lang_frame, text="EN", font=FONT_LANG, bg=LANG_SEL,
                  fg=ACCENT, padx=12, pady=4, cursor="hand2")
btn_en.pack(side="left")
tk.Frame(lang_frame, bg=BORDER, width=1).pack(side="left", fill="y")
btn_zh = tk.Label(lang_frame, text="中文", font=FONT_LANG, bg=LANG_BG,
                  fg=FG_DIM, padx=12, pady=4, cursor="hand2")
btn_zh.pack(side="left")


# ── 视图容器 ──────────────────────────────────────────────
container = tk.Frame(root, bg=BG)
container.pack(fill="both", expand=True, padx=20, pady=(8, 12))

select_view = tk.Frame(container, bg=BG)
chart_view  = tk.Frame(container, bg=BG)


def show_select():
    chart_view.pack_forget()
    select_view.pack(fill="both", expand=True)

def show_chart():
    select_view.pack_forget()
    chart_view.pack(fill="both", expand=True)


# ════════════════════════════════════════════════════════════
# 视图 1：选择
# ════════════════════════════════════════════════════════════
def make_section(parent):
    lbl = tk.Label(parent, text="", font=FONT_TITLE, bg=BG, fg=ACCENT)
    lbl.pack(anchor="w", pady=(8, 4))
    return lbl


# ── ① market 顶级切换 ───────────────────────────────────
lbl_market = make_section(select_view)

market_frame = tk.Frame(select_view, bg=BG)
market_frame.pack(fill="x")
market_btns = {}

def update_market_highlight():
    for mk, b in market_btns.items():
        sel = (mk == sel_market)
        b.configure(bg=SEL_BG if sel else BG_CARD,
                    fg=ACCENT if sel else FG,
                    highlightbackground=ACCENT if sel else BORDER)

def on_market_click(mk):
    """切换 market：刷新下方所有选择控件。当前 market 的状态保留。"""
    global sel_market
    if mk == sel_market:
        return
    sel_market = mk
    update_market_highlight()
    rebuild_symbol_picker()
    rebuild_iv_picker()
    rebuild_range_picker()
    # 持久化 market 选择，让下次启动记住
    try:
        cur = settings.load_settings()
        cur['market'] = sel_market
        settings.save_settings(cur)
    except Exception:
        pass

def rebuild_market_picker():
    for w in market_frame.winfo_children():
        w.destroy()
    market_btns.clear()
    cols = len(MARKETS)
    for i, mk in enumerate(MARKETS):
        btn = tk.Label(market_frame, text=t('mk_' + mk), font=FONT_OPTION,
                       bg=BG_CARD, fg=FG, cursor="hand2", pady=10,
                       highlightthickness=1, highlightbackground=BORDER)
        btn.grid(row=0, column=i, sticky="nsew", padx=2, pady=2)
        btn.bind("<Button-1>", lambda e, x=mk: on_market_click(x))
        market_btns[mk] = btn
    for c in range(cols):
        market_frame.columnconfigure(c, weight=1)
    update_market_highlight()


# ── ② 标的 ────────────────────────────────────────────────
lbl_symbol = make_section(select_view)

symbol_frame = tk.Frame(select_view, bg=BG)
symbol_frame.pack(fill="x")
symbol_btns = {}

def update_symbol_highlight():
    sel = current_symbol()
    for s, b in symbol_btns.items():
        b.configure(bg=SEL_BG if s == sel else BG_CARD,
                    fg=ACCENT if s == sel else FG)

def on_symbol_click(sym):
    cur_state()['sel_symbol'] = sym
    update_symbol_highlight()

def _short_for_display(symbol):
    """
    标的在按钮上的显示标签，按当前界面语言决定：
      - lang='zh' : 优先显示用户在 settings 里设的 cn_name；
                   缺失则查 plot_kline 内置表；最终 fallback 到 short
      - lang='en' : 显示 short（"BTC" / "MU" / "600519" / "0700"）
    """
    user_cn = cur_state().get('cn_name_map', {}).get(symbol, '')
    cfg = _resolve_symbol_config(sel_market, symbol, user_cn_name=user_cn)
    return cfg['cn_name'] if lang == 'zh' else cfg['short']

def rebuild_symbol_picker():
    """根据当前 market 的 symbols 重建按钮（market 切换或 settings 改动后调用）"""
    for w in symbol_frame.winfo_children():
        w.destroy()
    symbol_btns.clear()
    syms = current_symbols()
    if not syms:
        tk.Label(symbol_frame, text=t('no_symbols'), font=FONT_STATUS,
                 bg=BG, fg=FG_DIM, anchor='w').pack(fill='x', padx=2, pady=8)
        return
    cols = min(5, len(syms))
    for i, sym in enumerate(syms):
        r, c = divmod(i, cols)
        btn = tk.Label(symbol_frame, text=_short_for_display(sym), font=FONT_OPTION,
                       bg=BG_CARD, fg=FG, cursor="hand2", padx=8, pady=10,
                       highlightthickness=1, highlightbackground=BORDER)
        btn.grid(row=r, column=c, sticky="nsew", padx=2, pady=2)
        btn.bind("<Button-1>", lambda e, s=sym: on_symbol_click(s))
        symbol_btns[sym] = btn
    for c in range(cols):
        symbol_frame.columnconfigure(c, weight=1)
    update_symbol_highlight()


# ── ③ 入口周期 ────────────────────────────────────────
lbl_interval = make_section(select_view)

iv_frame = tk.Frame(select_view, bg=BG)
iv_frame.pack(fill="x")
iv_btns = {}

def update_iv_highlight():
    sel = current_interval()
    for iv, b in iv_btns.items():
        is_sel = (iv == sel)
        b.configure(bg=SEL_BG if is_sel else BG_CARD,
                    fg=ACCENT if is_sel else FG,
                    highlightbackground=ACCENT if is_sel else BORDER)

def on_iv_click(iv):
    cur_state()['sel_interval'] = iv
    update_iv_highlight()
    # 切换入口周期 → 时段列表跟着重建
    rebuild_range_picker()

def rebuild_iv_picker():
    """根据当前 market 的 ENTRY_INTERVALS 重建入口周期按钮"""
    for w in iv_frame.winfo_children():
        w.destroy()
    iv_btns.clear()
    ivs = current_entry_intervals()
    cols = len(ivs)
    for i, iv in enumerate(ivs):
        btn = tk.Label(iv_frame, text=t('iv_' + iv), font=FONT_OPTION,
                       bg=BG_CARD, fg=FG, cursor="hand2", pady=10,
                       highlightthickness=1, highlightbackground=BORDER)
        btn.grid(row=0, column=i, sticky="nsew", padx=2, pady=2)
        btn.bind("<Button-1>", lambda e, x=iv: on_iv_click(x))
        iv_btns[iv] = btn
    for c in range(cols):
        iv_frame.columnconfigure(c, weight=1)
    update_iv_highlight()


# ── ④ 时段 ────────────────────────────────────────────
lbl_range = make_section(select_view)

range_frame = tk.Frame(select_view, bg=BG)
range_frame.pack(fill="x")
range_btns = []

def select_range(idx):
    cur_state()['sel_rng_idx'][current_interval()] = idx
    for i, b in enumerate(range_btns):
        b.configure(bg=SEL_BG  if i == idx else BG_CARD,
                    fg=ACCENT2 if i == idx else FG,
                    highlightbackground=ACCENT2 if i == idx else BORDER)

def rebuild_range_picker():
    """根据当前 market+interval 对应的时段列表重建按钮"""
    for w in range_frame.winfo_children():
        w.destroy()
    range_btns.clear()
    rngs = current_ranges()
    if not rngs:
        tk.Label(range_frame, text=t('no_ranges'), font=FONT_STATUS,
                 bg=BG, fg=FG_DIM, anchor='w').pack(fill='x', padx=2, pady=8)
        return
    for idx, r in enumerate(rngs):
        btn = tk.Label(range_frame, text=f"  {r['label']}", font=FONT_OPTION,
                       bg=BG_CARD, fg=FG, anchor="w", pady=10, cursor="hand2",
                       highlightthickness=1, highlightbackground=BORDER)
        btn.pack(fill="x", padx=2, pady=2)
        btn.bind("<Button-1>", lambda e, i=idx: select_range(i))
        range_btns.append(btn)
    # 校正/恢复该周期上次选中的下标
    cur = current_rng_idx()
    cur = max(0, min(cur, len(range_btns) - 1))
    cur_state()['sel_rng_idx'][current_interval()] = cur
    select_range(cur)


def enter_chart():
    rngs = current_ranges()
    if not rngs or not current_symbols() or not current_symbol():
        return
    r = rngs[current_rng_idx()]
    start = parse_binance_str(r['start'])
    end   = parse_binance_str(r['end'])
    nav_stack.clear()
    render_and_push(sel_market, current_symbol(), current_interval(), start, end)

show_btn_frame = tk.Frame(select_view, bg=BG)
show_btn_frame.pack(pady=14)

show_btn = tk.Button(show_btn_frame, text="", font=FONT_BTN,
                     bg=BTN_BG, fg=BTN_FG,
                     activebackground=BTN_HOVER, activeforeground=BTN_FG,
                     relief="flat", bd=0, padx=32, pady=10,
                     cursor="hand2", command=enter_chart)
show_btn.pack()
show_btn.bind("<Enter>", lambda e: show_btn.configure(bg=BTN_HOVER))
show_btn.bind("<Leave>", lambda e: show_btn.configure(bg=BTN_BG))

status1_var = tk.StringVar(value="")
status1_lbl = tk.Label(select_view, textvariable=status1_var,
                       font=FONT_STATUS, bg=BG, fg=FG_DIM,
                       wraplength=620, justify="left")
status1_lbl.pack(pady=(0, 8))


# ════════════════════════════════════════════════════════════
# 视图 2：图表 + 钻取
# ════════════════════════════════════════════════════════════

crumb_holder = tk.Frame(chart_view, bg=BG)
crumb_holder.pack(fill="x", pady=(4, 8))
tk.Frame(chart_view, bg=BORDER, height=1).pack(fill="x")

lbl_drill = tk.Label(chart_view, text="", font=FONT_TITLE, bg=BG, fg=ACCENT)
lbl_drill.pack(anchor="w", pady=(12, 6))

sub_holder = tk.Frame(chart_view, bg=BG)
sub_holder.pack(fill="x")

reset_btn_frame = tk.Frame(chart_view, bg=BG)
reset_btn_frame.pack(pady=14)

def on_reset():
    nav_stack.clear()
    show_select()

reset_btn = tk.Button(reset_btn_frame, text="", font=FONT_BTN,
                      bg=BTN2_BG, fg=BTN_FG,
                      activebackground=BTN2_HOVER, activeforeground=BTN_FG,
                      relief="flat", bd=0, padx=32, pady=10,
                      cursor="hand2", command=on_reset)
reset_btn.pack()
reset_btn.bind("<Enter>", lambda e: reset_btn.configure(bg=BTN2_HOVER))
reset_btn.bind("<Leave>", lambda e: reset_btn.configure(bg=BTN2_BG))

status2_var = tk.StringVar(value="")
status2_lbl = tk.Label(chart_view, textvariable=status2_var,
                       font=FONT_STATUS, bg=BG, fg=FG_DIM,
                       wraplength=620, justify="left")
status2_lbl.pack(pady=(0, 8))


def _short_for_node(market, symbol):
    """面包屑里显示标的的短形态。"""
    if market == 'crypto' and symbol.endswith('USDT'):
        return symbol[:-4]
    return symbol


def build_crumbs():
    for w in crumb_holder.winfo_children():
        w.destroy()
    for idx, node in enumerate(nav_stack):
        is_last = (idx == len(nav_stack) - 1)
        short = _short_for_node(node['market'], node['symbol'])
        text = (f"{short} {t('iv_' + node['interval'])} · "
                f"{fmt_range(node['start'], node['end'], node['interval'])}"
                f"  [{node['bars']}b]")
        chip = tk.Label(crumb_holder, text=text, font=FONT_CRUMB,
                        bg=SEL_BG if is_last else BG_CARD,
                        fg=ACCENT2 if is_last else FG,
                        padx=8, pady=3,
                        highlightthickness=1,
                        highlightbackground=ACCENT2 if is_last else BORDER,
                        cursor="" if is_last else "hand2")
        chip.pack(side="left", padx=2, pady=2)
        if not is_last:
            chip.bind("<Button-1>", lambda e, i=idx: pop_to(i))
        if not is_last:
            sep = tk.Label(crumb_holder, text="▸", font=FONT_CRUMB,
                           bg=BG, fg=FG_DIM)
            sep.pack(side="left", padx=2)


def build_divgrid():
    """
    渲染"背离钻取"区,两种模式:

    1. 非锁定模式(栈顶 locked_anchor=None):
       列出本图所有检出的背离信号,每条一张卡片。点击 = 选定该背离作为
       "锁定锚点",钻取到次级别周期。

    2. 锁定模式(栈顶 locked_anchor 非空):
       单按钮"▸ 继续钻取下一级 (<下级周期>) @ <锁定时间>"。点击 = 用同一
       锚点继续钻往更细的下一级。锚点信息(含 s3_start/s3_end/peak_iso/kind)
       沿钻取链保持不变,直到面包屑回退到非锁定起点或点 Reset。

    末端周期(无 next_iv)显示"已到末端"提示。
    """
    for w in sub_holder.winfo_children():
        w.destroy()
    if not nav_stack:
        return
    top = nav_stack[-1]
    next_iv = nav_next_interval(top['interval'], top['market'])
    if not next_iv:
        lbl_drill.configure(text="")
        tip = tk.Label(sub_holder, text=t('end_msg'),
                       font=FONT_STATUS, bg=BG, fg=FG_DIM)
        tip.pack(anchor="w", pady=4)
        return

    lbl_drill.configure(text=t('div_drill'))

    # ── 锁定模式分支 ──────────────────────────────────────────────
    locked_anchor = top.get('locked_anchor')
    if locked_anchor:
        _render_locked_card(top, next_iv, locked_anchor)
        return

    # ── 非锁定模式:列出本图所有背离 ───────────────────────────────
    divs = top.get('divs', [])
    if not divs:
        tip = tk.Label(sub_holder, text=t('div_none'),
                       font=FONT_STATUS, bg=BG, fg=FG_DIM, anchor="w")
        tip.pack(fill="x", pady=2)
        return

    # 卡片格子:>=5 张 3 列,>=3 张 2 列,否则单列
    n = len(divs)
    cols = 3 if n >= 5 else (2 if n >= 3 else 1)
    for c in range(cols):
        sub_holder.columnconfigure(c, weight=1)

    for i, d in enumerate(divs):
        r, c = divmod(i, cols)
        # 卡片头色按 kind / provisional 决定
        provisional = d.get('provisional', False)
        if provisional:
            head_color = DIV_PROVISIONAL_FG
        elif d['kind'] == 'bullish':
            head_color = DIV_BULLISH_FG
        else:
            head_color = DIV_BEARISH_FG
        kind_text  = t('div_bullish') if d['kind'] == 'bullish' else t('div_bearish')
        is_extreme = d['level'] == 0
        prov_suffix = " ?" if provisional else ""
        ratio_pct  = int(round(d['ratio'] * 100))
        # peak 时间格式跟当前(父级)周期的颗粒度一致
        peak_str = _fmt_peak_for_interval(d['peak_iso'], top['interval'])

        card = tk.Frame(sub_holder, bg=BG_CARD,
                        highlightthickness=1, highlightbackground=BORDER)
        card.grid(row=r, column=c, sticky="nsew", padx=2, pady=2)

        # level 0 = 价格极值标识（标准三段背离漏掉的那类）：无 Lv / 无面积比
        if is_extreme:
            head_text = f"{i+1}/{n} · {kind_text} · {t('div_extreme')}"
        else:
            head_text = (f"{i+1}/{n} · {kind_text} · "
                         f"L{d['level']}  {ratio_pct}%{prov_suffix}")
        head = tk.Label(card, text=head_text,
                        font=FONT_SUB_S, bg=BG_CARD, fg=head_color, anchor="w")
        head.pack(fill="x", padx=10, pady=(6, 0))

        body_text = f"@ {peak_str}  ▸ {t('iv_' + next_iv)}"
        body = tk.Label(card, text=body_text,
                        font=FONT_SUB, bg=BG_CARD, fg=FG, anchor="w",
                        cursor=("arrow" if provisional else "hand2"))
        body.pack(fill="x", padx=10,
                  pady=(0, 6) if not provisional else (0, 0))

        # provisional(未完成)信号:卡片底部补一行灰字说明,讲清"为什么点不动"
        note = None
        if provisional:
            note = tk.Label(card, text=t('div_provisional'),
                            font=FONT_SUB_S, bg=BG_CARD, fg=FG_DIM, anchor="w")
            note.pack(fill="x", padx=10, pady=(0, 6))

        # 点击 = 用 div 的完整锚点(peak_iso + s3 时间窗 + kind + parent_interval)启动锁定链。
        # provisional(未完成)信号还未封口,不允许钻取——与公众号宣传一致,不绑定点击。
        if not provisional:
            for w in (card, head, body):
                w.bind("<Button-1>",
                       lambda evt, dd=d: _drill_with_anchor(
                           top['market'], top['symbol'], next_iv,
                           _div_to_anchor(dd, top['interval']),
                       ))
                w.configure(cursor="hand2")


def _render_locked_card(top, next_iv, locked_anchor):
    """锁定模式下的单按钮渲染。横贯整行,边框橙色提示锚定状态。"""
    sub_holder.columnconfigure(0, weight=1)
    peak_str = _fmt_peak_for_interval(locked_anchor.get('peak_iso', ''),
                                       top['interval'])

    card = tk.Frame(sub_holder, bg=BG_CARD,
                    highlightthickness=1, highlightbackground=DIV_BORDER)
    card.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)

    head = tk.Label(card, text=t('div_locked_head'),
                    font=FONT_SUB, bg=BG_CARD, fg=DIV_TITLE_FG, anchor="w")
    head.pack(fill="x", padx=12, pady=(8, 0))

    body_text = f"▸ {t('iv_' + next_iv)}    @ {peak_str}"
    body = tk.Label(card, text=body_text,
                    font=FONT_SUB, bg=BG_CARD, fg=FG, anchor="w",
                    cursor="hand2")
    body.pack(fill="x", padx=12, pady=(2, 8))

    # 点击 = 用同一锚点继续钻往更细的下一级
    for w in (card, head, body):
        w.bind("<Button-1>",
               lambda evt: _drill_with_anchor(
                   top['market'], top['symbol'], next_iv, locked_anchor))
        w.configure(cursor="hand2")


def _div_to_anchor(div, parent_interval):
    """从 serialize_divergences 输出的 div dict 抽取锚点信息包。

    parent_interval 是产出这条 div 的图所在周期。带在 anchor 里一路传给
    plot_kline 后,MA99 染色时用它把"父级 K 线 open_time"扩成"父级 K 线
    覆盖的物理时间窗",避免子图末段染色缺一根父级 K 线宽度的问题。
    """
    return {
        'peak_iso':     div['peak_iso'],
        's3_start_iso': div.get('s3_start_iso'),
        's3_end_iso':   div.get('s3_end_iso'),
        'kind':         div.get('kind', 'bullish'),
        'parent_interval': parent_interval,
    }


def _fmt_peak_for_interval(peak_iso, parent_interval):
    """按父级周期的颗粒度格式化 peak 时间——小时级带时分,日级及以上只日期。"""
    if not peak_iso:
        return ''
    try:
        peak_ts = _dt.datetime.fromisoformat(peak_iso)
    except (ValueError, TypeError):
        return peak_iso
    if parent_interval in ('15m', '30m', '1h', '4h'):
        return peak_ts.strftime('%Y-%m-%d %H:%M')
    return peak_ts.strftime('%Y-%m-%d')


def _drill_with_anchor(market, symbol, next_iv, anchor):
    """
    通用钻取入口:启动锁定链 / 沿锁定链继续都走这里。

    anchor: dict (peak_iso, s3_start_iso, s3_end_iso, kind)
        peak_iso 用于计算钻取窗口(peak ± BEFORE/AFTER 根次级 K 线)
        s3_start_iso / s3_end_iso 用于在次级别图上 MA99 染色
        kind 暂保留,未来若按方向区分染色色调时用得上

    一旦传入,就被 render_and_push 存进新栈帧的 locked_anchor 字段。
    """
    peak_ts = _dt.datetime.fromisoformat(anchor['peak_iso'])
    # 透传 S3 信息 → 启用自适应扩展窗口，让低级别图尽量罩住父级 S3 投影区
    win_start, win_end = compute_divergence_drill_window(
        peak_ts, next_iv, market,
        s3_start_iso=anchor.get('s3_start_iso'),
        s3_end_iso=anchor.get('s3_end_iso'),
        parent_iv=anchor.get('parent_interval'),
    )
    render_and_push(market, symbol, next_iv, win_start, win_end,
                    locked_anchor=anchor)


_BARS_RE = re.compile(r'^BARS=(\d+)\s*$', re.M)
_PATH_RE = re.compile(r'^图片已保存:\s*(.+?)\s*$', re.M)
_DIVS_RE = re.compile(r'^DIVS_JSON=(.+)$', re.M)


def render_and_push(market, symbol, interval, start, end, locked_anchor=None):
    """渲染窗口并推入导航栈。

    locked_anchor : dict | None
        锚点信息包(peak_iso + s3_start_iso + s3_end_iso + kind)。提供则:
          - subprocess 调 plot_kline 时序列化为 JSON 作 CLI 第 6 参数,
            plot_kline 据此在主面板 MA99 上染色;
          - 存入栈帧,UI 切换到"锁定单按钮"模式;
          - 面包屑回退弹栈后,旧栈帧若没有此字段,UI 回到"列出全部背离"模式。
    """
    show_chart()
    short = _short_for_node(market, symbol)
    status2_var.set(f"{t('generating')}  {short}  "
                    f"{t('iv_' + interval)}  "
                    f"{fmt_range(start, end, interval)} ...")
    status2_lbl.configure(fg=FG_DIM)
    root.update()

    script = os.path.join(_DIR, "plot_kline.py")
    # 锁定锚点透传给 plot_kline 子进程。空 anchor 传空串——plot_kline 按 falsy 跳过。
    anchor_arg = json.dumps(locked_anchor) if locked_anchor else ''
    cli_args = [sys.executable, script, market, symbol, interval,
                to_binance_str(start), to_binance_str(end),
                anchor_arg]
    result = subprocess.run(cli_args, capture_output=True, text=True)
    output = (result.stdout + result.stderr).strip()

    if result.returncode != 0:
        status2_var.set((output or t('error'))[-500:])
        status2_lbl.configure(fg=ERR_COLOR)
        return

    bars_match = _BARS_RE.search(result.stdout)
    path_match = _PATH_RE.search(result.stdout)
    if not bars_match:
        status2_var.set("Cannot parse BARS=N from plot output")
        status2_lbl.configure(fg=ERR_COLOR)
        return
    bars = int(bars_match.group(1))
    img_path = path_match.group(1) if path_match else None

    # 解析 DIVS_JSON=...(plot_kline.serialize_divergences 的输出)。
    # 失败不致命——退回到"无背离信号"显示。
    divs = []
    divs_match = _DIVS_RE.search(result.stdout)
    if divs_match:
        try:
            divs = json.loads(divs_match.group(1))
        except Exception:
            divs = []

    nav_stack.append({
        'market':   market,
        'symbol':   symbol,
        'interval': interval,
        'start':    start,
        'end':      end,
        'bars':     bars,
        # 背离信号列表(给 build_divgrid 用)
        'divs':     divs,
        # 锚点信息包(整条钻取链上保持不变,非锁定起点为 None)
        'locked_anchor': locked_anchor,
    })

    if img_path and os.path.exists(img_path):
        _open_image(img_path)
        status2_var.set(f"{t('opens')}  {os.path.basename(img_path)}  "
                        f"({bars} bars)")
        status2_lbl.configure(fg=OK_COLOR)
    else:
        status2_var.set(f"{t('done')}  ({bars} bars)")
        status2_lbl.configure(fg=OK_COLOR)

    build_crumbs()
    build_divgrid()


def pop_to(idx):
    """
    面包屑回弹:回到 idx 这一帧。如果 target 在锁定链中,恢复后 UI 仍是
    锁定模式;如果是非锁定起点,UI 回到"列出全部背离"模式——用户可以
    重新选另一条信号。
    """
    target = nav_stack[idx]
    del nav_stack[:]
    render_and_push(target['market'], target['symbol'], target['interval'],
                    target['start'], target['end'],
                    locked_anchor=target.get('locked_anchor'))


def _open_image(path):
    try:
        if sys.platform == 'darwin':
            subprocess.Popen(['open', path])
        elif sys.platform == 'win32':
            os.startfile(path)
        else:
            subprocess.Popen(['xdg-open', path])
    except Exception as e:
        print(f"[open image failed] {e}")


# ── 设置回调 ──────────────────────────────────────────────
def on_settings_saved(new_settings):
    """设置保存后被调，更新模块状态并重建 UI 控件。"""
    global lang, sel_market
    lang = new_settings.get('language', lang)
    new_market = new_settings.get('market', sel_market)
    if new_market not in MARKETS:
        new_market = sel_market

    # 多 market 状态全量刷新（保留 sel_symbol/sel_interval/sel_rng_idx 的合理值）
    for mk in MARKETS:
        block = new_settings.get(mk, {})
        sym_list = block.get('symbols', [])         # v4 dict 数组
        new_syms = settings.extract_tickers(sym_list)
        new_name_map = settings.extract_name_map(sym_list)
        ivs = get_entry_intervals(mk)
        new_ranges = {iv: list(block.get('ranges', {}).get(iv, [])) for iv in ivs}
        st = market_state[mk]
        st['symbols'] = new_syms
        st['cn_name_map'] = new_name_map
        st['ranges_all'] = new_ranges
        # sel_symbol 保留若仍存在，否则取首个
        if not new_syms:
            st['sel_symbol'] = None
        elif st['sel_symbol'] not in new_syms:
            st['sel_symbol'] = new_syms[0]
        # sel_interval 保留若仍合法，否则取首个
        if st['sel_interval'] not in ivs:
            st['sel_interval'] = ivs[0]
        # 各 interval 的 sel_rng_idx 校正
        new_idx = {}
        for iv in ivs:
            cur = st['sel_rng_idx'].get(iv, 0)
            new_idx[iv] = max(0, min(cur, max(0, len(new_ranges[iv]) - 1)))
        st['sel_rng_idx'] = new_idx

    sel_market = new_market

    rebuild_market_picker()
    rebuild_symbol_picker()
    rebuild_iv_picker()
    rebuild_range_picker()
    apply_lang()


def open_settings_clicked(event=None):
    open_settings_dialog(root, lang, sel_market, on_settings_saved)


settings_btn.bind("<Button-1>", open_settings_clicked)
settings_btn.bind("<Enter>", lambda e: settings_btn.configure(bg=SEL_BG))
settings_btn.bind("<Leave>", lambda e: settings_btn.configure(bg=BG_CARD))


# ── 语言切换 ──────────────────────────────────────────────
def apply_lang():
    root.title(t('title'))
    title_lbl.configure(text=f"📈 {t('title')}")
    lbl_market.configure(text=t('market'))
    lbl_symbol.configure(text=t('symbol'))
    lbl_interval.configure(text=t('interval'))
    lbl_range.configure(text=t('timerange'))
    show_btn.configure(text=t('show'))
    reset_btn.configure(text=t('reset'))
    btn_en.configure(bg=LANG_SEL if lang=='en' else LANG_BG,
                     fg=ACCENT   if lang=='en' else FG_DIM)
    btn_zh.configure(bg=LANG_SEL if lang=='zh' else LANG_BG,
                     fg=ACCENT   if lang=='zh' else FG_DIM)
    # market 按钮文字也跟语言变
    for mk, b in market_btns.items():
        b.configure(text=t('mk_' + mk))
    # 入口按钮文字也跟语言变
    for iv, b in iv_btns.items():
        b.configure(text=t('iv_' + iv))
    if not current_symbols():
        rebuild_symbol_picker()
    if not current_ranges():
        rebuild_range_picker()
    if nav_stack:
        build_crumbs()
        build_divgrid()


def switch_lang(new_lang):
    """顶栏快速切换：立刻应用 + 持久化到 user_settings.json。"""
    global lang
    lang = new_lang
    apply_lang()
    try:
        cur = settings.load_settings()
        cur['language'] = new_lang
        settings.save_settings(cur)
    except Exception:
        pass


btn_en.bind("<Button-1>", lambda e: switch_lang('en'))
btn_zh.bind("<Button-1>", lambda e: switch_lang('zh'))


# ── 初始化 ────────────────────────────────────────────────
rebuild_market_picker()
rebuild_symbol_picker()
rebuild_iv_picker()
rebuild_range_picker()
apply_lang()
show_select()
root.mainloop()
