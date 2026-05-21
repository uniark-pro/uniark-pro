"""
K-line analysis — desktop UI (navigation-stack version + dynamic
segment count + settings).
==================================================================
Workflow:
  1. Pick a symbol → pick an entry interval (weekly / 3day / daily) →
     pick a time range → click "Generate Chart"
  2. A subprocess generates the PNG and the system image viewer
     opens it. The UI switches to navigation view.
  3. Parse BARS=N from plot_kline.py's stdout, then by formula
     segments = floor(N_next / BARS_PER_SEGMENT_TARGET) + 1
     decide how many sub-segments the window should be sliced into.
  4. One button per segment (clickable even if there is just 1).
     Clicking enters the next level.
  5. Drill all the way to 15m at the pyramid bottom.
  6. Click a breadcrumb to return to any upper level; click Reset to
     return to the selection view.

Entry intervals:
  ENTRY_INTERVALS (from the settings module) defines the entries
  allowed in the main UI. Each entry has its own independent list of
  time ranges (settings.ranges[interval]). Other intervals (4h, 1h,
  ...) are reachable only by drilling, never as a top-level entry.

Settings:
  The ⚙ button in the top bar opens the settings dialog: language,
  symbol list, and time-range list per entry interval can all be
  edited there. Changes are written to user_settings.json and
  auto-loaded on next launch. app.py shares the same settings file.
"""
import tkinter as tk
import subprocess
import sys
import os
import re
import datetime as _dt

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)

from navigation import (NEXT_INTERVAL,
                        compute_segment_count, compute_subranges,
                        INTERVAL_MINUTES, BARS_PER_SEGMENT_TARGET)
import settings
from settings import ENTRY_INTERVALS
from settings_dialog_tk import open_settings_dialog


# ── Color palette ─────────────────────────────────────────
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


SYMBOLS = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'FILUSDT')


# ── i18n ──────────────────────────────────────────────────
I18N = {
    'en': {
        'title':     "K-Line",
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
        'end_msg':   "(End of pyramid — 15m has no further drill-down)",
        'iv_15m':    "15m",  'iv_30m':"30m", 'iv_1h':"1h", 'iv_4h':"4h",
        'iv_daily':  "Daily",'iv_3day':"3-Day", 'iv_weekly':"Weekly",
        'no_symbols':"No symbols configured. Click ⚙ to add one.",
        'no_ranges': "No time ranges configured for this interval. Click ⚙ to add one.",
    },
    'zh': {
        'title':     "K线",
        'symbol':    "▸  币种",
        'interval':  "▸  周期",
        'timerange': "▸  时间段",
        'show':      "生成图表",
        'drill':     "▸  点击进入下一级周期",
        'reset':     "↺  重新开始",
        'generating':"正在生成",
        'opens':     "正在打开",
        'done':      "完成 ✓",
        'error':     "错误 ✗",
        'end_msg':   "(已到金字塔末端 — 15分钟下方无更细周期)",
        'iv_15m':    "15分钟", 'iv_30m':"30分钟", 'iv_1h':"1小时", 'iv_4h':"4小时",
        'iv_daily':  "日线", 'iv_3day':"3日线", 'iv_weekly':"周线",
        'no_symbols':"未配置币种，点击 ⚙ 添加。",
        'no_ranges': "该周期未配置时间段，点击 ⚙ 添加。",
    }
}


# ── Utilities ─────────────────────────────────────────────
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


# ── Module state (loaded from settings; mutable via the
#    settings dialog at runtime) ───────────────────────────
_initial    = settings.load_settings()
lang        = _initial['language']
SYMBOLS     = list(_initial['symbols'])
# RANGES_ALL is now {interval: list[range]}; pick by current entry interval.
RANGES_ALL  = {iv: list(_initial['ranges'].get(iv, [])) for iv in ENTRY_INTERVALS}
sel_symbol  = SYMBOLS[0] if SYMBOLS else None
sel_interval = ENTRY_INTERVALS[0]   # default: weekly
# Each entry interval keeps its own selected-range index, so switching
# entries remembers position. Default: last item (most recent range).
sel_rng_idx_by_iv = {
    iv: len(RANGES_ALL[iv]) - 1 if RANGES_ALL[iv] else 0
    for iv in ENTRY_INTERVALS
}
nav_stack   = []


def current_ranges():
    """Time-range list for the currently selected entry interval."""
    return RANGES_ALL.get(sel_interval, [])

def current_rng_idx():
    return sel_rng_idx_by_iv.get(sel_interval, 0)


# ── Main window ───────────────────────────────────────────
root = tk.Tk()
root.configure(bg=BG)
root.geometry("680x820")
root.resizable(False, False)


def t(key):
    return I18N[lang][key]


# ── Top bar ───────────────────────────────────────────────
top_bar = tk.Frame(root, bg=BG)
top_bar.pack(fill="x", padx=20, pady=(12, 0))

title_lbl = tk.Label(top_bar, text="", font=("Noto Sans", 14, "bold"),
                     bg=BG, fg=ACCENT)
title_lbl.pack(side="left")

# Top bar right: ⚙ settings + language switch
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


# ── View container ────────────────────────────────────────
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
# View 1: selection
# ════════════════════════════════════════════════════════════
def make_section(parent):
    lbl = tk.Label(parent, text="", font=FONT_TITLE, bg=BG, fg=ACCENT)
    lbl.pack(anchor="w", pady=(8, 4))
    return lbl

# ── Symbol ────────────────────────────────────────────────
lbl_symbol = make_section(select_view)

symbol_frame = tk.Frame(select_view, bg=BG)
symbol_frame.pack(fill="x")
symbol_btns = {}

def update_symbol_highlight():
    for s, b in symbol_btns.items():
        b.configure(bg=SEL_BG if s == sel_symbol else BG_CARD,
                    fg=ACCENT if s == sel_symbol else FG)

def on_symbol_click(sym):
    global sel_symbol
    sel_symbol = sym
    update_symbol_highlight()

def rebuild_symbol_picker():
    """Rebuild buttons based on current SYMBOLS (called after settings
    save)."""
    for w in symbol_frame.winfo_children():
        w.destroy()
    symbol_btns.clear()
    if not SYMBOLS:
        tk.Label(symbol_frame, text=t('no_symbols'), font=FONT_STATUS,
                 bg=BG, fg=FG_DIM, anchor='w').pack(fill='x', padx=2, pady=8)
        return
    cols = min(5, len(SYMBOLS))
    for i, sym in enumerate(SYMBOLS):
        r, c = divmod(i, cols)
        short = sym.replace('USDT', '')
        btn = tk.Label(symbol_frame, text=short, font=FONT_OPTION,
                       bg=BG_CARD, fg=FG, cursor="hand2", padx=8, pady=10,
                       highlightthickness=1, highlightbackground=BORDER)
        btn.grid(row=r, column=c, sticky="nsew", padx=2, pady=2)
        btn.bind("<Button-1>", lambda e, s=sym: on_symbol_click(s))
        symbol_btns[sym] = btn
    for c in range(cols):
        symbol_frame.columnconfigure(c, weight=1)
    update_symbol_highlight()


# ── Entry interval ────────────────────────────────────────
lbl_interval = make_section(select_view)

iv_frame = tk.Frame(select_view, bg=BG)
iv_frame.pack(fill="x")
iv_btns = {}

def update_iv_highlight():
    for iv, b in iv_btns.items():
        sel = (iv == sel_interval)
        b.configure(bg=SEL_BG if sel else BG_CARD,
                    fg=ACCENT if sel else FG,
                    highlightbackground=ACCENT if sel else BORDER)

def on_iv_click(iv):
    global sel_interval
    sel_interval = iv
    update_iv_highlight()
    # When the entry interval changes, the range list rebuilds.
    rebuild_range_picker()

def rebuild_iv_picker():
    """Rebuild entry-interval buttons from ENTRY_INTERVALS (called when
    language changes or at init)."""
    for w in iv_frame.winfo_children():
        w.destroy()
    iv_btns.clear()
    cols = len(ENTRY_INTERVALS)
    for i, iv in enumerate(ENTRY_INTERVALS):
        btn = tk.Label(iv_frame, text=t('iv_' + iv), font=FONT_OPTION,
                       bg=BG_CARD, fg=FG, cursor="hand2", pady=10,
                       highlightthickness=1, highlightbackground=BORDER)
        btn.grid(row=0, column=i, sticky="nsew", padx=2, pady=2)
        btn.bind("<Button-1>", lambda e, x=iv: on_iv_click(x))
        iv_btns[iv] = btn
    for c in range(cols):
        iv_frame.columnconfigure(c, weight=1)
    update_iv_highlight()


# ── Range ─────────────────────────────────────────────────
lbl_range = make_section(select_view)

range_frame = tk.Frame(select_view, bg=BG)
range_frame.pack(fill="x")
range_btns = []

def select_range(idx):
    sel_rng_idx_by_iv[sel_interval] = idx
    for i, b in enumerate(range_btns):
        b.configure(bg=SEL_BG  if i == idx else BG_CARD,
                    fg=ACCENT2 if i == idx else FG,
                    highlightbackground=ACCENT2 if i == idx else BORDER)

def rebuild_range_picker():
    """Rebuild buttons from the time-range list of the currently
    selected entry interval."""
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
    # Clamp / restore the previously selected index for this interval.
    cur = current_rng_idx()
    cur = max(0, min(cur, len(range_btns) - 1))
    sel_rng_idx_by_iv[sel_interval] = cur
    select_range(cur)


def enter_chart():
    rngs = current_ranges()
    if not rngs or not SYMBOLS:
        return
    r = rngs[current_rng_idx()]
    start = parse_binance_str(r['start'])
    end   = parse_binance_str(r['end'])
    nav_stack.clear()
    render_and_push(sel_symbol, sel_interval, start, end)

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
# View 2: chart + drill-down
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


def build_crumbs():
    for w in crumb_holder.winfo_children():
        w.destroy()
    for idx, node in enumerate(nav_stack):
        is_last = (idx == len(nav_stack) - 1)
        short = node['symbol'].replace('USDT', '')
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
    next_iv = NEXT_INTERVAL.get(top['interval'])
    if not next_iv:
        lbl_drill.configure(text="")
        tip = tk.Label(sub_holder, text=t('end_msg'),
                       font=FONT_STATUS, bg=BG, fg=FG_DIM)
        tip.pack(anchor="w", pady=4)
        return

    seg_count = compute_segment_count(top['interval'], top['bars'])
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
                   lambda evt, ss=s, ee=e: render_and_push(top['symbol'], next_iv, ss, ee))
            w.configure(cursor="hand2")


_BARS_RE = re.compile(r'^BARS=(\d+)\s*$', re.M)
# Match the English path-output line from plot_kline.py's CLI.
_PATH_RE = re.compile(r'^Image saved:\s*(.+?)\s*$', re.M)


def render_and_push(symbol, interval, start, end):
    show_chart()
    status2_var.set(f"{t('generating')}  {symbol.replace('USDT','')}  "
                    f"{t('iv_' + interval)}  "
                    f"{fmt_range(start, end, interval)} ...")
    status2_lbl.configure(fg=FG_DIM)
    root.update()

    script = os.path.join(_DIR, "plot_kline.py")
    result = subprocess.run(
        [sys.executable, script, symbol, interval,
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
    render_and_push(target['symbol'], target['interval'],
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


# ── Settings callback ─────────────────────────────────────
def on_settings_saved(new_settings):
    """Called after settings are saved; updates module state and
    rebuilds UI widgets."""
    global SYMBOLS, RANGES_ALL, sel_symbol, lang
    SYMBOLS = list(new_settings['symbols'])
    RANGES_ALL = {iv: list(new_settings['ranges'].get(iv, []))
                  for iv in ENTRY_INTERVALS}
    if not SYMBOLS:
        sel_symbol = None
    elif sel_symbol not in SYMBOLS:
        sel_symbol = SYMBOLS[0]
    # Indices for each entry may overflow (entries deleted) — clamp.
    for iv in ENTRY_INTERVALS:
        cur = sel_rng_idx_by_iv.get(iv, 0)
        sel_rng_idx_by_iv[iv] = max(0, min(cur, max(0, len(RANGES_ALL[iv]) - 1)))
    lang = new_settings['language']
    rebuild_symbol_picker()
    rebuild_iv_picker()
    rebuild_range_picker()
    apply_lang()


def open_settings_clicked(event=None):
    open_settings_dialog(root, lang, on_settings_saved)


settings_btn.bind("<Button-1>", open_settings_clicked)
settings_btn.bind("<Enter>", lambda e: settings_btn.configure(bg=SEL_BG))
settings_btn.bind("<Leave>", lambda e: settings_btn.configure(bg=BG_CARD))


# ── Language switch ───────────────────────────────────────
def apply_lang():
    root.title(t('title'))
    title_lbl.configure(text=f"📈 {t('title')}")
    lbl_symbol.configure(text=t('symbol'))
    lbl_interval.configure(text=t('interval'))
    lbl_range.configure(text=t('timerange'))
    show_btn.configure(text=t('show'))
    reset_btn.configure(text=t('reset'))
    btn_en.configure(bg=LANG_SEL if lang=='en' else LANG_BG,
                     fg=ACCENT   if lang=='en' else FG_DIM)
    btn_zh.configure(bg=LANG_SEL if lang=='zh' else LANG_BG,
                     fg=ACCENT   if lang=='zh' else FG_DIM)
    # Entry-button text follows the language too
    for iv, b in iv_btns.items():
        b.configure(text=t('iv_' + iv))
    if not SYMBOLS:
        rebuild_symbol_picker()
    if not current_ranges():
        rebuild_range_picker()
    if nav_stack:
        build_crumbs()
        build_subgrid()


def switch_lang(new_lang):
    """Top-bar quick switch: apply immediately and persist to
    user_settings.json."""
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


# ── Init ──────────────────────────────────────────────────
rebuild_symbol_picker()
rebuild_iv_picker()
rebuild_range_picker()
apply_lang()
show_select()
root.mainloop()
