"""
桌面端设置对话框（Tkinter Toplevel，多 market 版）
====================================================
独立成文件，让 main.py 保持薄。

对外只导出一个函数：
    open_settings_dialog(parent, lang, initial_market, on_save_callback)

对话框分四段：语言 / market / 标的 / 时间段。
- market 段：[虚拟币 | 美股 | A股 | 港股] 四 tab，决定下面"标的"和"时间段"
  当前在编辑哪个 market 的数据。四个 market 各自独立维护自己的
  symbols 和 ranges。
- 标的段：当前 market 的标的列表 + 添加表单
- 时间段段：按当前 market 的入口周期分 tab，每 tab 一份独立列表

保存时校验"每个 market × 每个入口周期至少 1 条时间段"，
然后整体写入 settings.SETTINGS_FILE，调 on_save_callback(new_settings)。
"""
import tkinter as tk
from tkinter import messagebox

import settings
from settings import MARKETS, ENTRY_INTERVALS_BY_MARKET, get_entry_intervals


# ── 配色（与 main.py 同一套）─────────────────────────────────────────
BG        = "#1e1e2e"
BG_CARD   = "#2a2a3e"
ACCENT    = "#7c6af7"
FG        = "#e0e0f0"
FG_DIM    = "#8888aa"
SEL_BG    = "#3a3a5e"
BTN_BG    = "#7c6af7"
BTN_FG    = "#ffffff"
BTN_HOVER = "#9b8dff"
BTN2_BG   = "#44446a"
BTN2_HOVER= "#5a5a7a"
BORDER    = "#44446a"
DEL_BG    = "#5a3a3a"
DEL_HOVER = "#7a4a4a"

FONT_TITLE  = ("Noto Sans", 11, "bold")
FONT_OPTION = ("Noto Sans", 11)
FONT_BTN    = ("Noto Sans", 10, "bold")
FONT_SMALL  = ("Noto Sans", 9)
FONT_HINT   = ("Noto Sans", 8)


I18N = {
    'en': {
        'title':       "Settings",
        'language':    "▸  Language",
        'market':      "▸  Market",
        'symbols':     "▸  Symbols",
        'ranges':      "▸  Time Ranges",
        'add':         "Add",
        'save':        "Save",
        'cancel':      "Cancel",
        'sym_ph_crypto':   "Symbol (e.g. BTCUSDT)",
        'sym_ph_us_stock': "Ticker (e.g. MU, NVDA)",
        'sym_ph_cn_stock': "Ticker (e.g. 600519.SS, 000001.SZ)",
        'sym_ph_hk_stock': "Ticker (e.g. 0700.HK, 9988.HK)",
        'sym_ph_cn':       "Name (optional)",
        'lbl_ph':      "Label (e.g. 2024-01 ~ 2024-12)",
        'start_ph':    "Start date (e.g. 17 Aug, 2017)",
        'end_ph':      "End date (e.g. 30 Dec, 2022)",
        'date_hint':   "Date format: 'DD Mon, YYYY'  e.g. '17 Aug, 2017'",
        'sym_min':     "At least one symbol is required.",
        'rng_min':     "At least one time range is required for this interval.",
        'date_bad':    "Invalid date format. Use 'DD Mon, YYYY' e.g. '17 Aug, 2017'.",
        'sym_dup':     "Symbol already exists.",
        'sym_empty':   "Please enter a symbol.",
        'rng_empty':   "Please fill all three fields.",
        'date_order':  "End date must be after start date.",
        'save_err':    "Save failed",
        'mk_crypto':   "Crypto",
        'mk_us_stock': "US Stocks",
        'mk_cn_stock': "A-Shares",
        'mk_hk_stock': "HK Stocks",
        'iv_15m':      "15m",  'iv_30m':"30m", 'iv_1h':"1h", 'iv_4h':"4h",
        'iv_daily':    "Daily",'iv_3day':"3-Day", 'iv_weekly':"Weekly",
    },
    'zh': {
        'title':       "设置",
        'language':    "▸  语言",
        'market':      "▸  市场",
        'symbols':     "▸  标的",
        'ranges':      "▸  时间段",
        'add':         "添加",
        'save':        "保存",
        'cancel':      "取消",
        'sym_ph_crypto':   "币种（例 BTCUSDT）",
        'sym_ph_us_stock': "代码（例 MU、NVDA）",
        'sym_ph_cn_stock': "代码（例 600519.SS、000001.SZ）",
        'sym_ph_hk_stock': "代码（例 0700.HK、9988.HK）",
        'sym_ph_cn':       "中文名（选填）",
        'lbl_ph':      "标签（例 2024-01 ~ 2024-12）",
        'start_ph':    "起始日期（例 17 Aug, 2017）",
        'end_ph':      "结束日期（例 30 Dec, 2022）",
        'date_hint':   "日期格式：'DD Mon, YYYY'  例如 '17 Aug, 2017'",
        'sym_min':     "至少需要保留一个标的。",
        'rng_min':     "该周期至少需要保留一个时间段。",
        'date_bad':    "日期格式不正确。请使用 'DD Mon, YYYY'，例如 '17 Aug, 2017'。",
        'sym_dup':     "该标的已存在。",
        'sym_empty':   "请输入标的代码。",
        'rng_empty':   "请填写所有三个字段。",
        'date_order':  "结束日期必须晚于起始日期。",
        'save_err':    "保存失败",
        'mk_crypto':   "虚拟币",
        'mk_us_stock': "美股",
        'mk_cn_stock': "A股",
        'mk_hk_stock': "港股",
        'iv_15m':      "15分钟", 'iv_30m':"30分钟", 'iv_1h':"1小时", 'iv_4h':"4小时",
        'iv_daily':    "日线", 'iv_3day':"3日线", 'iv_weekly':"周线",
    }
}


# ── Entry 占位符（Tk Entry 没原生支持，自己模拟）────────────────────
def _attach_placeholder(entry, text):
    """在 Entry 上模拟 placeholder：失焦时灰字提示，聚焦或有输入时清掉。"""
    def show_ph():
        entry.delete(0, 'end')
        entry.insert(0, text)
        entry.configure(fg=FG_DIM)
        entry._is_placeholder = True

    def on_focus_in(_e):
        if getattr(entry, '_is_placeholder', False):
            entry.delete(0, 'end')
            entry.configure(fg=FG)
            entry._is_placeholder = False

    def on_focus_out(_e):
        if not entry.get().strip():
            show_ph()

    entry.bind('<FocusIn>', on_focus_in)
    entry.bind('<FocusOut>', on_focus_out)
    show_ph()


def _entry_value(entry):
    """读 Entry 真实值，placeholder 状态视为空。"""
    if getattr(entry, '_is_placeholder', False):
        return ''
    return entry.get().strip()


def _clear_entry(entry, ph_text):
    """清空 Entry 并重新显示 placeholder。"""
    entry.delete(0, 'end')
    entry.configure(fg=FG_DIM)
    entry.insert(0, ph_text)
    entry._is_placeholder = True


# ── 主入口 ───────────────────────────────────────────────────────────
def open_settings_dialog(parent, lang, initial_market, on_save_callback):
    """
    打开设置对话框。

    Parameters
    ----------
    parent : tk.Tk | tk.Toplevel
    lang : str
        当前语言 'en' | 'zh'，决定对话框初始文案。
    initial_market : str
        打开对话框时默认选中的 market（'crypto' | 'us_stock' | 'cn_stock' | 'hk_stock'）。
    on_save_callback : callable(new_settings: dict)
        保存成功时被调，参数是写入后的设置 dict。
    """
    cur = settings.load_settings()

    # 编辑态：每个 market 一份 syms / rngs_by_iv，互不影响
    # syms_by_market[market] = [{'ticker': str, 'cn_name': str}, ...]（v4 结构）
    syms_by_market = {
        m: [dict(s) for s in cur[m]['symbols']] for m in MARKETS
    }
    rngs_by_market = {
        m: {iv: [dict(r) for r in cur[m]['ranges'].get(iv, [])]
            for iv in get_entry_intervals(m)}
        for m in MARKETS
    }

    win = tk.Toplevel(parent)
    win.title(I18N[lang]['title'])
    win.configure(bg=BG)
    win.geometry("680x900")
    win.transient(parent)
    # 强制 Tk 处理所有 pending 几何/重绘事件，让窗口真正 realize 后再 grab。
    # 不加这一行在某些平台上（特别是 Linux X11）grab_set 可能时序不稳，
    # 表现为对话框偶尔焦点抢夺失败、键盘事件丢失。
    win.update_idletasks()
    win.grab_set()

    lang_var = tk.StringVar(value=cur['language'])
    market_var = tk.StringVar(value=initial_market if initial_market in MARKETS
                                    else cur.get('market', MARKETS[0]))
    # 当前在编辑哪个 market × 哪个入口周期的时段（tab 选中状态）
    rng_iv_var = tk.StringVar(
        value=get_entry_intervals(market_var.get())[0]
    )

    def L(key):
        return I18N[lang_var.get()][key]

    def M():
        return market_var.get()

    # ── 滚动容器 ─────────────────────────────────────────────────
    canvas = tk.Canvas(win, bg=BG, highlightthickness=0)
    scrollbar = tk.Scrollbar(win, orient='vertical', command=canvas.yview)
    body = tk.Frame(canvas, bg=BG)
    canvas.configure(yscrollcommand=scrollbar.set)

    btn_bar = tk.Frame(win, bg=BG)
    btn_bar.pack(side='bottom', fill='x', padx=20, pady=12)

    canvas.pack(side='left', fill='both', expand=True, padx=20, pady=(12, 0))
    scrollbar.pack(side='right', fill='y')
    canvas_window = canvas.create_window((0, 0), window=body, anchor='nw')

    def _on_canvas_resize(event):
        canvas.itemconfig(canvas_window, width=event.width)
    canvas.bind('<Configure>', _on_canvas_resize)
    body.bind('<Configure>',
              lambda e: canvas.configure(scrollregion=canvas.bbox('all')))

    def _on_wheel(event):
        canvas.yview_scroll(-int(event.delta / 60), 'units')
    canvas.bind_all('<MouseWheel>', _on_wheel)
    canvas.bind_all('<Button-4>', lambda e: canvas.yview_scroll(-3, 'units'))
    canvas.bind_all('<Button-5>', lambda e: canvas.yview_scroll(3, 'units'))

    # ── ① 语言 ───────────────────────────────────────────────────
    lbl_lang = tk.Label(body, text="", font=FONT_TITLE, bg=BG, fg=ACCENT)
    lbl_lang.pack(anchor='w', pady=(8, 4))

    lang_frame = tk.Frame(body, bg=BG)
    lang_frame.pack(fill='x')

    btn_en_set = tk.Label(lang_frame, text="EN", font=FONT_OPTION,
                          bg=BG_CARD, fg=FG, padx=20, pady=10, cursor='hand2',
                          highlightthickness=1, highlightbackground=BORDER)
    btn_en_set.pack(side='left', padx=(0, 4))

    btn_zh_set = tk.Label(lang_frame, text="中文", font=FONT_OPTION,
                          bg=BG_CARD, fg=FG, padx=20, pady=10, cursor='hand2',
                          highlightthickness=1, highlightbackground=BORDER)
    btn_zh_set.pack(side='left')

    def update_lang_btns():
        for code, btn in (('en', btn_en_set), ('zh', btn_zh_set)):
            sel = (lang_var.get() == code)
            btn.configure(bg=SEL_BG if sel else BG_CARD,
                          fg=ACCENT if sel else FG,
                          highlightbackground=ACCENT if sel else BORDER)

    def set_lang(code):
        lang_var.set(code)
        update_lang_btns()
        apply_dialog_lang()

    btn_en_set.bind('<Button-1>', lambda e: set_lang('en'))
    btn_zh_set.bind('<Button-1>', lambda e: set_lang('zh'))

    # ── ② market（决定下面"标的"和"时段"在编辑哪个 market）──
    lbl_market = tk.Label(body, text="", font=FONT_TITLE, bg=BG, fg=ACCENT)
    lbl_market.pack(anchor='w', pady=(16, 4))

    market_tabs_frame = tk.Frame(body, bg=BG)
    market_tabs_frame.pack(fill='x')
    market_tab_btns = {}

    def update_market_tab_highlight():
        cur_m = M()
        for m, b in market_tab_btns.items():
            sel = (m == cur_m)
            b.configure(bg=SEL_BG if sel else BG_CARD,
                        fg=ACCENT if sel else FG,
                        highlightbackground=ACCENT if sel else BORDER)

    def on_market_tab(m):
        if m == M():
            return
        market_var.set(m)
        update_market_tab_highlight()
        rng_iv_var.set(get_entry_intervals(m)[0])
        if getattr(sym_entry, '_is_placeholder', False):
            sym_entry.delete(0, 'end')
            sym_entry.insert(0, _sym_ph())
        rebuild_iv_tabs()
        render_syms()
        render_rngs()

    def rebuild_market_tabs():
        for w in market_tabs_frame.winfo_children():
            w.destroy()
        market_tab_btns.clear()
        cols = len(MARKETS)
        for i, m in enumerate(MARKETS):
            btn = tk.Label(market_tabs_frame, text=L('mk_' + m),
                           font=FONT_OPTION, bg=BG_CARD, fg=FG,
                           cursor='hand2', pady=8,
                           highlightthickness=1, highlightbackground=BORDER)
            btn.grid(row=0, column=i, sticky='nsew', padx=2, pady=2)
            btn.bind('<Button-1>', lambda e, x=m: on_market_tab(x))
            market_tab_btns[m] = btn
        for c in range(cols):
            market_tabs_frame.columnconfigure(c, weight=1)
        update_market_tab_highlight()

    # ── ③ 标的（当前 market 的列表）─────────────────────────
    lbl_syms = tk.Label(body, text="", font=FONT_TITLE, bg=BG, fg=ACCENT)
    lbl_syms.pack(anchor='w', pady=(16, 4))

    syms_list_frame = tk.Frame(body, bg=BG)
    syms_list_frame.pack(fill='x')

    sym_add_frame = tk.Frame(body, bg=BG)
    sym_add_frame.pack(fill='x', pady=(6, 0))

    # 标的输入：ticker（必填）+ cn_name（选填）。两个 Entry 横向排开，
    # ticker 占 60% 宽，cn_name 占 40%，再加 + 按钮。手机端窄屏可能
    # 显示挤，但 Tk 桌面端 680px 窗口完全够。
    sym_entry = tk.Entry(sym_add_frame, font=FONT_OPTION,
                         bg=BG_CARD, fg=FG_DIM, insertbackground=FG,
                         highlightthickness=1, highlightbackground=BORDER,
                         relief='flat')
    sym_entry.pack(side='left', fill='x', expand=True, ipady=8, padx=(0, 4))

    cn_entry = tk.Entry(sym_add_frame, font=FONT_OPTION, width=14,
                        bg=BG_CARD, fg=FG_DIM, insertbackground=FG,
                        highlightthickness=1, highlightbackground=BORDER,
                        relief='flat')
    cn_entry.pack(side='left', ipady=8, padx=(0, 6))

    sym_add_btn = tk.Button(sym_add_frame, text="", font=FONT_BTN,
                            bg=BTN_BG, fg=BTN_FG, relief='flat', bd=0,
                            activebackground=BTN_HOVER, activeforeground=BTN_FG,
                            padx=20, pady=8, cursor='hand2')
    sym_add_btn.pack(side='right')

    def _sym_ph():
        return L('sym_ph_' + M())

    def _cn_ph():
        return L('sym_ph_cn')

    def cur_sym_list():
        return syms_by_market[M()]

    def render_syms():
        from plot_kline import _resolve_symbol_config
        for w in syms_list_frame.winfo_children():
            w.destroy()
        for entry in cur_sym_list():
            ticker = entry.get('ticker', '')
            user_cn = entry.get('cn_name', '')
            row = tk.Frame(syms_list_frame, bg=BG_CARD,
                           highlightthickness=1, highlightbackground=BORDER)
            row.pack(fill='x', pady=2)
            # 用 _resolve_symbol_config 算最终显示名（user > builtin > short）
            cfg = _resolve_symbol_config(M(), ticker, user_cn_name=user_cn)
            short = cfg['short']
            cn = cfg['cn_name']
            # ticker · 中文名 双行显示。仅当 cn 跟 ticker / short 都不一样
            # 时才拼"·"——避免 'BTC · BTC' 这种重复噪音。
            if cn and cn != ticker and cn != short:
                label_text = f"{ticker}  ·  {cn}"
            else:
                label_text = ticker
            tk.Label(row, text=label_text, font=FONT_OPTION, bg=BG_CARD, fg=FG,
                     anchor='w', padx=12, pady=8
                     ).pack(side='left', fill='x', expand=True)
            del_btn = tk.Label(row, text="✕", font=FONT_BTN,
                               bg=DEL_BG, fg=FG, padx=14, pady=8, cursor='hand2')
            del_btn.pack(side='right')
            del_btn.bind('<Button-1>', lambda e, tk_=ticker: remove_sym(tk_))
            del_btn.bind('<Enter>', lambda e, b=del_btn: b.configure(bg=DEL_HOVER))
            del_btn.bind('<Leave>', lambda e, b=del_btn: b.configure(bg=DEL_BG))

    def add_sym():
        ticker = _entry_value(sym_entry).upper()
        cn = _entry_value(cn_entry)
        if not ticker:
            messagebox.showwarning(L('title'), L('sym_empty'), parent=win)
            return
        # 用 ticker 字段去重，跟旧版本逻辑一致
        existing_tickers = {s.get('ticker', '') for s in cur_sym_list()}
        if ticker in existing_tickers:
            messagebox.showwarning(L('title'), L('sym_dup'), parent=win)
            return
        cur_sym_list().append({'ticker': ticker, 'cn_name': cn})
        _clear_entry(sym_entry, _sym_ph())
        _clear_entry(cn_entry, _cn_ph())
        render_syms()

    def remove_sym(ticker):
        if len(cur_sym_list()) <= 1:
            messagebox.showwarning(L('title'), L('sym_min'), parent=win)
            return
        # 按 ticker 字段查找并删除
        cur_sym_list()[:] = [s for s in cur_sym_list()
                              if s.get('ticker', '') != ticker]
        render_syms()

    sym_add_btn.configure(command=add_sym)
    sym_entry.bind('<Return>', lambda e: add_sym())
    cn_entry.bind('<Return>', lambda e: add_sym())

    # ── ④ 时间段（按当前 market 的入口周期分 tab）──────────
    lbl_rngs = tk.Label(body, text="", font=FONT_TITLE, bg=BG, fg=ACCENT)
    lbl_rngs.pack(anchor='w', pady=(16, 4))

    tabs_frame = tk.Frame(body, bg=BG)
    tabs_frame.pack(fill='x')
    iv_tab_btns = {}

    def select_iv_tab(iv):
        rng_iv_var.set(iv)
        update_iv_tab_highlight()
        render_rngs()

    def update_iv_tab_highlight():
        cur_iv = rng_iv_var.get()
        for iv, b in iv_tab_btns.items():
            sel = (iv == cur_iv)
            b.configure(bg=SEL_BG if sel else BG_CARD,
                        fg=ACCENT if sel else FG,
                        highlightbackground=ACCENT if sel else BORDER)

    def rebuild_iv_tabs():
        """language 或 market 切换时重建。"""
        for w in tabs_frame.winfo_children():
            w.destroy()
        iv_tab_btns.clear()
        ivs = get_entry_intervals(M())
        if rng_iv_var.get() not in ivs:
            rng_iv_var.set(ivs[0])
        cols = len(ivs)
        for i, iv in enumerate(ivs):
            btn = tk.Label(tabs_frame, text=L('iv_' + iv), font=FONT_OPTION,
                           bg=BG_CARD, fg=FG, cursor='hand2', pady=8,
                           highlightthickness=1, highlightbackground=BORDER)
            btn.grid(row=0, column=i, sticky='nsew', padx=2, pady=2)
            btn.bind('<Button-1>', lambda e, x=iv: select_iv_tab(x))
            iv_tab_btns[iv] = btn
        for c in range(cols):
            tabs_frame.columnconfigure(c, weight=1)
        update_iv_tab_highlight()

    rngs_list_frame = tk.Frame(body, bg=BG)
    rngs_list_frame.pack(fill='x', pady=(4, 0))

    rng_add_frame = tk.Frame(body, bg=BG)
    rng_add_frame.pack(fill='x', pady=(6, 0))

    lbl_entry = tk.Entry(rng_add_frame, font=FONT_OPTION,
                         bg=BG_CARD, fg=FG_DIM, insertbackground=FG,
                         highlightthickness=1, highlightbackground=BORDER,
                         relief='flat')
    lbl_entry.pack(fill='x', ipady=6, pady=2)

    start_entry = tk.Entry(rng_add_frame, font=FONT_OPTION,
                           bg=BG_CARD, fg=FG_DIM, insertbackground=FG,
                           highlightthickness=1, highlightbackground=BORDER,
                           relief='flat')
    start_entry.pack(fill='x', ipady=6, pady=2)

    end_entry = tk.Entry(rng_add_frame, font=FONT_OPTION,
                         bg=BG_CARD, fg=FG_DIM, insertbackground=FG,
                         highlightthickness=1, highlightbackground=BORDER,
                         relief='flat')
    end_entry.pack(fill='x', ipady=6, pady=2)

    rng_hint = tk.Label(rng_add_frame, text="", font=FONT_HINT,
                        bg=BG, fg=FG_DIM, anchor='w')
    rng_hint.pack(fill='x', pady=(4, 2))

    rng_add_btn = tk.Button(rng_add_frame, text="", font=FONT_BTN,
                            bg=BTN_BG, fg=BTN_FG, relief='flat', bd=0,
                            activebackground=BTN_HOVER, activeforeground=BTN_FG,
                            padx=20, pady=8, cursor='hand2')
    rng_add_btn.pack(anchor='e', pady=(2, 0))

    def cur_rng_list():
        return rngs_by_market[M()][rng_iv_var.get()]

    def render_rngs():
        for w in rngs_list_frame.winfo_children():
            w.destroy()
        for i, r in enumerate(cur_rng_list()):
            row = tk.Frame(rngs_list_frame, bg=BG_CARD,
                           highlightthickness=1, highlightbackground=BORDER)
            row.pack(fill='x', pady=2)
            txt = f"{r['label']}\n{r['start']}  →  {r['end']}"
            tk.Label(row, text=txt, font=FONT_SMALL, bg=BG_CARD, fg=FG,
                     anchor='w', justify='left', padx=12, pady=8
                     ).pack(side='left', fill='x', expand=True)
            del_btn = tk.Label(row, text="✕", font=FONT_BTN,
                               bg=DEL_BG, fg=FG, padx=14, pady=8, cursor='hand2')
            del_btn.pack(side='right')
            del_btn.bind('<Button-1>', lambda e, idx=i: remove_rng(idx))
            del_btn.bind('<Enter>', lambda e, b=del_btn: b.configure(bg=DEL_HOVER))
            del_btn.bind('<Leave>', lambda e, b=del_btn: b.configure(bg=DEL_BG))

    def add_rng():
        label = _entry_value(lbl_entry)
        start = _entry_value(start_entry)
        end_  = _entry_value(end_entry)
        if not (label and start and end_):
            messagebox.showwarning(L('title'), L('rng_empty'), parent=win)
            return
        try:
            s_dt = settings.validate_date_str(start)
            e_dt = settings.validate_date_str(end_)
        except Exception:
            messagebox.showwarning(L('title'), L('date_bad'), parent=win)
            return
        if e_dt <= s_dt:
            messagebox.showwarning(L('title'), L('date_order'), parent=win)
            return
        cur_rng_list().append({'label': label, 'start': start, 'end': end_})
        _clear_entry(lbl_entry,   L('lbl_ph'))
        _clear_entry(start_entry, L('start_ph'))
        _clear_entry(end_entry,   L('end_ph'))
        render_rngs()

    def remove_rng(idx):
        if len(cur_rng_list()) <= 1:
            messagebox.showwarning(L('title'), L('rng_min'), parent=win)
            return
        del cur_rng_list()[idx]
        render_rngs()

    rng_add_btn.configure(command=add_rng)

    # ── 底部 Save / Cancel ──────────────────────────────────────
    cancel_btn = tk.Button(btn_bar, text="", font=FONT_BTN,
                           bg=BTN2_BG, fg=BTN_FG, relief='flat', bd=0,
                           activebackground=BTN2_HOVER, activeforeground=BTN_FG,
                           padx=24, pady=8, cursor='hand2',
                           command=lambda: _close())
    cancel_btn.pack(side='right', padx=(8, 0))

    save_btn = tk.Button(btn_bar, text="", font=FONT_BTN,
                         bg=BTN_BG, fg=BTN_FG, relief='flat', bd=0,
                         activebackground=BTN_HOVER, activeforeground=BTN_FG,
                         padx=24, pady=8, cursor='hand2')
    save_btn.pack(side='right')

    def do_save():
        # 校验：每个 market × 每个入口周期都至少要有 1 条 range，且至少 1 个标的
        for m in MARKETS:
            if len(syms_by_market[m]) == 0:
                market_var.set(m)
                update_market_tab_highlight()
                rebuild_iv_tabs()
                render_syms()
                render_rngs()
                messagebox.showwarning(
                    L('title'),
                    f"{L('mk_' + m)} — {L('sym_min')}",
                    parent=win,
                )
                return
            for iv in get_entry_intervals(m):
                if len(rngs_by_market[m][iv]) == 0:
                    market_var.set(m)
                    rng_iv_var.set(iv)
                    update_market_tab_highlight()
                    rebuild_iv_tabs()
                    render_syms()
                    render_rngs()
                    messagebox.showwarning(
                        L('title'),
                        f"{L('mk_' + m)} / {L('iv_' + iv)} — {L('rng_min')}",
                        parent=win,
                    )
                    return

        new_settings = {
            'language': lang_var.get(),
            'market':   market_var.get(),
        }
        for m in MARKETS:
            new_settings[m] = {
                # v4 dict 数组深拷贝（避免外部修改污染编辑态）
                'symbols': [dict(s) for s in syms_by_market[m]],
                'ranges': {iv: [dict(r) for r in rngs_by_market[m][iv]]
                           for iv in get_entry_intervals(m)},
            }
        try:
            settings.save_settings(new_settings)
        except Exception as e:
            messagebox.showerror(L('save_err'), str(e), parent=win)
            return
        on_save_callback(new_settings)
        _close()

    save_btn.configure(command=do_save)

    # ── 关闭：解绑全局滚轮 ──────────────────────────────────────
    def _close():
        try:
            canvas.unbind_all('<MouseWheel>')
            canvas.unbind_all('<Button-4>')
            canvas.unbind_all('<Button-5>')
        except Exception:
            pass
        win.destroy()

    win.protocol('WM_DELETE_WINDOW', _close)

    # ── 文案应用（语言切换时也调用） ─────────────────────────────
    def apply_dialog_lang():
        win.title(L('title'))
        lbl_lang.configure(text=L('language'))
        lbl_market.configure(text=L('market'))
        lbl_syms.configure(text=L('symbols'))
        lbl_rngs.configure(text=L('ranges'))
        sym_add_btn.configure(text=L('add'))
        rng_add_btn.configure(text=L('add'))
        save_btn.configure(text=L('save'))
        cancel_btn.configure(text=L('cancel'))
        rng_hint.configure(text=L('date_hint'))
        for m, b in market_tab_btns.items():
            b.configure(text=L('mk_' + m))
        for ent, ph_key in ((sym_entry,   None),
                            (cn_entry,    'sym_ph_cn'),
                            (lbl_entry,   'lbl_ph'),
                            (start_entry, 'start_ph'),
                            (end_entry,   'end_ph')):
            if ph_key is None:
                ph_text = _sym_ph()
            else:
                ph_text = L(ph_key)
            if getattr(ent, '_is_placeholder', False):
                ent.delete(0, 'end')
                ent.insert(0, ph_text)
            else:
                if not hasattr(ent, '_is_placeholder'):
                    _attach_placeholder(ent, ph_text)
        rebuild_iv_tabs()

    # 初次渲染
    rebuild_market_tabs()
    apply_dialog_lang()
    update_lang_btns()
    render_syms()
    render_rngs()
