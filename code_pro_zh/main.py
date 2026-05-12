"""
K线分析 - 桌面 UI（导航栈版 + 动态段数 + 设置 + 多 market）
============================================================
工作流：
  1. 选 market（虚拟币/美股/A股/港股）→ 选标的 → 选入口周期 → 选时段 → 点 "Generate Chart"
  2. subprocess 生成 PNG，系统看图器打开；UI 切到导航视图
  3. 从 plot_kline.py 的 stdout 解析 BARS=N，按公式
     段数 = floor(N_next/BARS_PER_SEGMENT_TARGET)+1 算应切几段
  4. 每段一个按钮（即使 1 段也要点击）；点了进入下一级
  5. 一路钻到金字塔末端（crypto 是 15m，三个 stock market 都是 1h）
  6. 点面包屑回到任一上层；点 Reset 回到选择视图

入口周期（按 market 不同）：
  - crypto                          : weekly / 3day / daily
  - us_stock / cn_stock / hk_stock  : weekly / daily
  其余周期（4h、1h 等）只能通过钻取访问，不在主界面顶级出现。

设置：
  顶栏 ⚙ 按钮打开设置对话框，可改语言、当前 market 的标的列表、
  当前 market 各入口周期的时段列表。改动写入 user_settings.json，
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

from navigation import (next_interval as nav_next_interval,
                        compute_segment_count, compute_subranges,
                        INTERVAL_MINUTES, BARS_PER_SEGMENT_TARGET)
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
        'drill':     "▸  Drill into next-level sub-segments",
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
    },
    'zh': {
        'title':     "K线",
        'market':    "▸  市场",
        'symbol':    "▸  标的",
        'interval':  "▸  周期",
        'timerange': "▸  时间段",
        'show':      "生成图表",
        'drill':     "▸  点击进入下一级周期",
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


def build_subgrid():
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

    seg_count = compute_segment_count(top['interval'], top['bars'], top['market'])
    subs = compute_subranges(top['start'], top['end'], seg_count)

    est_bars = int(top['bars'] * INTERVAL_MINUTES[top['interval']]
                                / INTERVAL_MINUTES[next_iv] / seg_count)
    if lang == 'en':
        lbl_drill.configure(text=f"▸  Drill into {next_iv} sub-segments (~{est_bars} bars each)")
    else:
        lbl_drill.configure(text=f"▸  进入下一级周期（每段约 {est_bars} 根）")

    if seg_count >= 5:
        cols = 3
    elif seg_count >= 3:
        cols = 2
    else:
        cols = 1
    for c in range(cols):
        sub_holder.columnconfigure(c, weight=1)

    for i, (s, e) in enumerate(subs):
        r, c = divmod(i, cols)
        card = tk.Frame(sub_holder, bg=BG_CARD,
                        highlightthickness=1, highlightbackground=BORDER)
        card.grid(row=r, column=c, sticky="nsew", padx=2, pady=2)

        head = tk.Label(card, text=f"{i+1}/{seg_count} · {t('iv_' + next_iv)}",
                        font=FONT_SUB_S, bg=BG_CARD, fg=FG_DIM, anchor="w")
        head.pack(fill="x", padx=10, pady=(6, 0))
        body = tk.Label(card, text=fmt_range(s, e, next_iv),
                        font=FONT_SUB, bg=BG_CARD, fg=FG, anchor="w",
                        cursor="hand2")
        body.pack(fill="x", padx=10, pady=(0, 6))
        for w in (card, head, body):
            w.bind("<Button-1>",
                   lambda evt, ss=s, ee=e: render_and_push(top['market'], top['symbol'], next_iv, ss, ee))
            w.configure(cursor="hand2")


_BARS_RE = re.compile(r'^BARS=(\d+)\s*$', re.M)
_PATH_RE = re.compile(r'^图片已保存:\s*(.+?)\s*$', re.M)


def render_and_push(market, symbol, interval, start, end):
    show_chart()
    short = _short_for_node(market, symbol)
    status2_var.set(f"{t('generating')}  {short}  "
                    f"{t('iv_' + interval)}  "
                    f"{fmt_range(start, end, interval)} ...")
    status2_lbl.configure(fg=FG_DIM)
    root.update()

    script = os.path.join(_DIR, "plot_kline.py")
    result = subprocess.run(
        [sys.executable, script, market, symbol, interval,
         to_binance_str(start), to_binance_str(end)],
        capture_output=True, text=True
    )
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

    nav_stack.append({
        'market':   market,
        'symbol':   symbol,
        'interval': interval,
        'start':    start,
        'end':      end,
        'bars':     bars,
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
    build_subgrid()


def pop_to(idx):
    target = nav_stack[idx]
    del nav_stack[:]
    render_and_push(target['market'], target['symbol'], target['interval'],
                    target['start'], target['end'])


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
        build_subgrid()


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
