"""
K-line analysis — Web App (with settings panel).
=================================================
Workflow: pick a symbol → pick an entry interval (weekly / 3day /
daily) → pick a time range → view the chart.
Drill-down: dynamically compute the segment count from the current
chart's K-line count: segments = floor(N_next / 385) + 1.
The /generate endpoint returns the image, the actual window, and the
bar count.

Entry intervals:
  ENTRY_INTERVALS (from the settings module) defines the entry
  intervals allowed in the main UI. Each entry has its own
  independent list of time ranges (settings.ranges[interval]).

Settings:
  The ⚙ button in the top bar opens the settings view, where the
  language, symbol list, and per-entry-interval time ranges can be
  edited. Changes are persisted to user_settings.json via POST
  /settings, shared with main.py.
"""
import os
import sys
import io
import base64
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from flask import Flask, render_template_string, request, jsonify

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)

from plot_kline import render_chart, SYMBOL_CONFIG
from navigation import (NEXT_INTERVAL, INTERVAL_MINUTES,
                        BARS_PER_SEGMENT_TARGET)
import settings
from settings import ENTRY_INTERVALS

app = Flask(__name__)


def generate_chart(symbol, interval, start_str, end_str):
    """Render and return (b64, actual_start_iso, actual_end_iso, bars_count)."""
    fig, df, _ = render_chart(symbol, interval,
                              start_str or None, end_str or None)
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight', pad_inches=0.8)
    plt.close(fig)
    buf.seek(0)
    img_b64 = base64.b64encode(buf.read()).decode('utf-8')
    return (img_b64,
            df.index[0].isoformat(),
            df.index[-1].isoformat(),
            int(len(df)))


HTML = r"""
<!DOCTYPE html>
<html lang="en" id="html-root">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>K-Line Generator</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #1e1e2e; color: #e0e0f0;
           font-family: 'Segoe UI', 'Noto Sans', sans-serif;
           min-height: 100vh; padding: 20px 16px; }

    .top-bar { display: flex; justify-content: space-between;
               align-items: center; margin-bottom: 16px; gap: 8px; }
    h1 { font-size: 1.25rem; color: #7c6af7; }

    .top-right { display: flex; align-items: center; gap: 8px; }

    .settings-btn { background: #2a2a3e; border: 1px solid #44446a;
                    border-radius: 6px; padding: 4px 12px;
                    font-size: 1.2rem; cursor: pointer; user-select: none;
                    transition: all 0.15s; color: #e0e0f0; line-height: 1.2; }
    .settings-btn:hover { border-color: #7c6af7; color: #7c6af7; }

    .lang-switcher { display: flex; border: 1px solid #44446a;
                     border-radius: 6px; overflow: hidden; }
    .lang-btn { padding: 5px 14px; cursor: pointer; font-size: 0.85rem;
                font-weight: bold; background: #2a2a3e; color: #8888aa;
                transition: all 0.2s; user-select: none; }
    .lang-btn.active { background: #3a3a5e; color: #7c6af7; }
    .lang-divider { width: 1px; background: #44446a; }

    .section-title { font-size: 0.82rem; color: #7c6af7; font-weight: bold;
                     text-transform: uppercase; letter-spacing: 1px;
                     margin-bottom: 8px; margin-top: 14px; }

    .grid { display: grid; gap: 6px; margin-bottom: 6px; }
    .grid.symbols  { grid-template-columns: repeat(auto-fit, minmax(80px, 1fr)); }
    .grid.intervals{ grid-template-columns: repeat(3, 1fr); }
    .opt { background: #2a2a3e; border: 1px solid #44446a;
           border-radius: 8px; padding: 13px 8px; text-align: center;
           cursor: pointer; font-size: 1rem; transition: all 0.15s;
           user-select: none; }
    .opt:hover { border-color: #7c6af7; }
    .opt.active { background: #3a3a5e; color: #7c6af7;
                  border-color: #7c6af7; font-weight: bold; }

    .empty-hint { color: #8888aa; font-size: 0.85rem;
                  padding: 12px; text-align: center;
                  border: 1px dashed #44446a; border-radius: 8px; }

    .range-list { display: flex; flex-direction: column;
                  gap: 8px; margin-bottom: 18px; }
    .range-item { background: #2a2a3e; border: 1px solid #44446a;
                  border-radius: 8px; padding: 13px 16px; cursor: pointer;
                  font-size: 1rem; transition: all 0.15s; user-select: none; }
    .range-item:hover { border-color: #f7a26a; }
    .range-item.active { background: #3a3a5e; color: #f7a26a;
                         border-color: #f7a26a; font-weight: bold; }

    .btn { width: 100%; padding: 14px; background: #7c6af7; color: white;
           border: none; border-radius: 8px; font-size: 1.05rem;
           font-weight: bold; cursor: pointer; transition: background 0.2s;
           margin-bottom: 14px; }
    .btn:hover   { background: #9b8dff; }
    .btn:disabled{ background: #44446a; cursor: not-allowed; }
    .btn-secondary { background: #44446a; }
    .btn-secondary:hover { background: #5a5a7a; }

    /* drill-down */
    .crumbs { display: flex; flex-wrap: wrap; gap: 4px; align-items: center;
              padding: 10px 0; margin-bottom: 8px;
              border-bottom: 1px solid #44446a; }
    .crumb { background: #2a2a3e; border: 1px solid #44446a;
             border-radius: 5px; padding: 5px 10px; font-size: 0.84rem;
             cursor: pointer; user-select: none; transition: all 0.15s; }
    .crumb:hover { border-color: #7c6af7; color: #7c6af7; }
    .crumb.current { background: #3a3a5e; color: #f7a26a;
                     border-color: #f7a26a; cursor: default; }
    .crumb.current:hover { color: #f7a26a; }
    .crumb-sep { color: #8888aa; font-size: 0.8rem; }

    .sub-section { margin-top: 14px; }
    .sub-hint { font-size: 0.78rem; color: #8888aa; margin-bottom: 6px; }

    .grid.subs       { grid-template-columns: 1fr; gap: 6px; }
    .grid.subs.cols2 { grid-template-columns: repeat(2, 1fr); }
    .grid.subs.cols3 { grid-template-columns: repeat(3, 1fr); }

    .sub-card { background: #2a2a3e; border: 1px solid #44446a;
                border-radius: 8px; padding: 10px; text-align: left;
                cursor: pointer; transition: all 0.15s; user-select: none; }
    .sub-card:hover { border-color: #f7a26a; }
    .sub-card-head { font-size: 0.75rem; color: #8888aa; margin-bottom: 2px; }
    .sub-card-body { font-size: 0.92rem; color: #e0e0f0; }

    #status-1, #status-2, #status-set { text-align: center; font-size: 0.85rem;
                            color: #8888aa; margin: 8px 0; min-height: 1.1em; }
    #status-1.ok, #status-2.ok, #status-set.ok    { color: #50fa7b; }
    #status-1.err, #status-2.err, #status-set.err { color: #ff5555; }

    #chart-wrap { text-align: center; margin: 8px 0; }
    #chart-img  { max-width: 100%; border-radius: 8px;
                  box-shadow: 0 4px 20px rgba(0,0,0,0.5); }

    .spinner { display: none; margin: 16px auto; width: 32px; height: 32px;
               border: 3px solid #44446a; border-top-color: #7c6af7;
               border-radius: 50%; animation: spin 0.8s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }

    .view { display: none; }
    .view.active { display: block; }

    /* ── Settings view ───────────────────────────────────── */
    .settings-row { background: #2a2a3e; border: 1px solid #44446a;
                    border-radius: 8px; padding: 10px 12px;
                    display: flex; align-items: center; gap: 8px;
                    margin-bottom: 6px; }
    .settings-row .row-text { flex: 1; font-size: 0.94rem; line-height: 1.4; }
    .settings-row .row-text small { color: #8888aa; font-size: 0.78rem;
                                     display: block; margin-top: 2px; }
    .del-btn { background: #5a3a3a; color: #e0e0f0; border: none;
               border-radius: 5px; padding: 6px 12px; cursor: pointer;
               font-weight: bold; transition: background 0.15s;
               flex-shrink: 0; }
    .del-btn:hover { background: #7a4a4a; }

    .input { width: 100%; background: #2a2a3e; border: 1px solid #44446a;
             border-radius: 8px; padding: 10px 12px; color: #e0e0f0;
             font-size: 0.95rem; margin-bottom: 6px;
             font-family: inherit; }
    .input:focus { outline: none; border-color: #7c6af7; }
    .input::placeholder { color: #6a6a8a; }

    .add-form { background: #232336; border: 1px dashed #44446a;
                border-radius: 8px; padding: 10px; margin-top: 4px; }
    .add-form .hint { font-size: 0.75rem; color: #8888aa;
                      margin: 2px 0 6px 0; }

    .add-btn { padding: 9px 18px; background: #7c6af7; color: white;
               border: none; border-radius: 6px; font-weight: bold;
               cursor: pointer; transition: background 0.15s; }
    .add-btn:hover { background: #9b8dff; }

    .lang-row { display: flex; gap: 6px; margin-bottom: 6px; }
    .lang-row .opt { flex: 1; }

    .iv-tabs { display: grid; grid-template-columns: repeat(3, 1fr);
               gap: 6px; margin-bottom: 8px; }
    .iv-tabs .opt { padding: 9px 8px; }

    .footer-actions { display: flex; gap: 8px; margin-top: 18px; }
    .footer-actions .btn { flex: 1; margin-bottom: 0; }
  </style>
</head>
<body>

  <div class="top-bar">
    <h1 id="title">📈 K-Line Generator</h1>
    <div class="top-right">
      <div class="settings-btn" id="btn-settings"
           title="Settings" onclick="openSettings()">⚙</div>
      <div class="lang-switcher">
        <div class="lang-btn active" id="btn-en" onclick="switchLang('en', true)">EN</div>
        <div class="lang-divider"></div>
        <div class="lang-btn"        id="btn-zh" onclick="switchLang('zh', true)">中文</div>
      </div>
    </div>
  </div>

  <!-- ═══════ View 1: pick symbol + entry interval + range ═════════════ -->
  <div id="view-select" class="view active">
    <div class="section-title" id="lbl-symbol">Symbol</div>
    <div class="grid symbols" id="symbol-grid"></div>

    <div class="section-title" id="lbl-interval">Interval</div>
    <div class="grid intervals" id="interval-grid"></div>

    <div class="section-title" id="lbl-range">Time Range</div>
    <div class="range-list" id="range-list"></div>

    <button class="btn" id="gen-btn" onclick="enterChart()">Generate Chart</button>
    <div id="status-1"></div>
    <div class="spinner" id="spinner-1"></div>
  </div>

  <!-- ═══════ View 2: chart + drill-down ═══════════════════════════════ -->
  <div id="view-chart" class="view">
    <div class="crumbs" id="crumbs"></div>
    <div id="chart-wrap"><img id="chart-img" src=""></div>
    <div id="status-2"></div>
    <div class="spinner" id="spinner-2"></div>

    <div class="sub-section" id="sub-section">
      <div class="sub-hint" id="sub-hint">Drill into a sub-segment:</div>
      <div class="grid subs" id="sub-grid"></div>
    </div>

    <button class="btn btn-secondary" onclick="resetToStart()">
      <span id="btn-reset-text">↺ Start over</span>
    </button>
  </div>

  <!-- ═══════ View 3: settings ═════════════════════════════════════════ -->
  <div id="view-settings" class="view">
    <div class="section-title" id="lbl-set-language">Language</div>
    <div class="lang-row">
      <div class="opt" id="set-lang-en" onclick="setSettingsLang('en')">EN</div>
      <div class="opt" id="set-lang-zh" onclick="setSettingsLang('zh')">中文</div>
    </div>

    <div class="section-title" id="lbl-set-symbols">Symbols</div>
    <div id="set-syms-list"></div>
    <div class="add-form">
      <input type="text" class="input" id="set-sym-input"
             placeholder="Symbol (e.g. BTCUSDT)">
      <button class="add-btn" id="btn-set-sym-add"
              onclick="addSettingSym()">Add</button>
    </div>

    <div class="section-title" id="lbl-set-ranges">Time Ranges</div>
    <div class="iv-tabs" id="set-iv-tabs"></div>
    <div id="set-rngs-list"></div>
    <div class="add-form">
      <input type="text" class="input" id="set-rng-label"
             placeholder="Label (e.g. 2024-01 ~ 2024-12)">
      <input type="text" class="input" id="set-rng-start"
             placeholder="Start date (e.g. 17 Aug, 2017)">
      <input type="text" class="input" id="set-rng-end"
             placeholder="End date (e.g. 30 Dec, 2022)">
      <div class="hint" id="set-rng-hint">Date format: 'DD Mon, YYYY' e.g. '17 Aug, 2017'</div>
      <button class="add-btn" id="btn-set-rng-add"
              onclick="addSettingRng()">Add</button>
    </div>

    <div id="status-set"></div>

    <div class="footer-actions">
      <button class="btn btn-secondary" id="btn-set-cancel"
              onclick="cancelSettings()">Cancel</button>
      <button class="btn" id="btn-set-save"
              onclick="saveSettings()">Save</button>
    </div>
  </div>

<script>
  // ── Server-injected globals ────────────────────────────────────────
  let SYMBOLS                    = {{ symbols_json | safe }};
  // RANGES_BY_IV: { weekly: [...], 3day: [...], daily: [...] }
  let RANGES_BY_IV               = {{ ranges_json | safe }};
  const ENTRY_INTERVALS          = {{ entry_intervals_json | safe }};
  const NEXT_INTERVAL            = {{ next_interval_json | safe }};
  const INTERVAL_MINUTES         = {{ interval_minutes_json | safe }};
  const BARS_PER_SEGMENT_TARGET  = {{ target_json | safe }};
  let curLang                    = {{ language_json | safe }};

  // ── i18n ──────────────────────────────────────────────────────────
  const i18n = {
    en: {
      title:      '📈 K-Line Generator',
      symbol:     'SYMBOL',
      interval:   'INTERVAL',
      timerange:  'TIME RANGE',
      generate:   'Generate Chart',
      drill_hint: bars => `Drill into ${bars}-bar sub-segments:`,
      reset:      '↺ Start over',
      generating: 'Generating...',
      done:       bars => `Done ✓ (${bars} bars)`,
      error:      'Error ✗',
      end_msg:    '(End of pyramid — 15m has no further drill-down)',
      iv: { '15m':'15m','30m':'30m','1h':'1h','4h':'4h',
            'daily':'Daily','3day':'3-Day','weekly':'Weekly' },
      // settings
      set_language: 'LANGUAGE',
      set_symbols:  'SYMBOLS',
      set_ranges:   'TIME RANGES',
      set_save:     'Save',
      set_cancel:   'Cancel',
      set_add:      'Add',
      set_sym_ph:   'Symbol (e.g. BTCUSDT)',
      set_lbl_ph:   'Label (e.g. 2024-01 ~ 2024-12)',
      set_start_ph: 'Start date (e.g. 17 Aug, 2017)',
      set_end_ph:   'End date (e.g. 30 Dec, 2022)',
      set_hint:     "Date format: 'DD Mon, YYYY'  e.g. '17 Aug, 2017'",
      set_saved:    'Settings saved ✓',
      set_save_err: 'Save failed',
      sym_dup:      'Symbol already exists',
      sym_min:      'At least one symbol is required',
      rng_min:      iv => `${iv}: at least one time range is required`,
      rng_empty:    'Please fill all three fields',
      date_bad:     "Invalid date format. Use 'DD Mon, YYYY'.",
      date_order:   'End date must be after start date',
      no_symbols:   'No symbols configured.',
      no_ranges:    'No time ranges for this interval.',
    },
    zh: {
      title:      '📈 K线生成器',
      symbol:     '币种',
      interval:   '周期',
      timerange:  '时间段',
      generate:   '生成图表',
      drill_hint: bars => `点击进入下一级（每段约 ${bars} 根 K 线）：`,
      reset:      '↺ 重新开始',
      generating: '正在生成...',
      done:       bars => `完成 ✓ (${bars} 根)`,
      error:      '错误 ✗',
      end_msg:    '(已到金字塔末端 — 15分钟下方无更细周期)',
      iv: { '15m':'15分钟','30m':'30分钟','1h':'1小时','4h':'4小时',
            'daily':'日线','3day':'3日线','weekly':'周线' },
      // settings
      set_language: '语言',
      set_symbols:  '币种',
      set_ranges:   '时间段',
      set_save:     '保存',
      set_cancel:   '取消',
      set_add:      '添加',
      set_sym_ph:   '币种（例 BTCUSDT）',
      set_lbl_ph:   '标签（例 2024-01 ~ 2024-12）',
      set_start_ph: '起始日期（例 17 Aug, 2017）',
      set_end_ph:   '结束日期（例 30 Dec, 2022）',
      set_hint:     "日期格式：'DD Mon, YYYY'  例如 '17 Aug, 2017'",
      set_saved:    '设置已保存 ✓',
      set_save_err: '保存失败',
      sym_dup:      '该币种已存在',
      sym_min:      '至少需要保留一个币种',
      rng_min:      iv => `${iv}：该周期至少需要保留一个时间段`,
      rng_empty:    '请填写所有三个字段',
      date_bad:     "日期格式不正确。请使用 'DD Mon, YYYY' 格式。",
      date_order:   '结束日期必须晚于起始日期',
      no_symbols:   '未配置币种。',
      no_ranges:    '该周期未配置时间段。',
    }
  };

  // ── State ─────────────────────────────────────────────────────────
  let selSymbol  = SYMBOLS[0] || null;
  let selInterval = ENTRY_INTERVALS[0];   // default = first = weekly
  // Each entry interval keeps its own selected-range index
  let selRngIdxByIv = {};
  ENTRY_INTERVALS.forEach(iv => {
    // Default to last item (most recent range)
    selRngIdxByIv[iv] = (RANGES_BY_IV[iv] || []).length - 1;
    if (selRngIdxByIv[iv] < 0) selRngIdxByIv[iv] = 0;
  });
  let stack = [];          // navigation stack
  let prevView = 'view-select';   // where the settings view returns to

  // Settings panel temporary draft (discarded on cancel)
  let draftLang = curLang;
  let draftSyms = [];
  let draftRngs = {};       // {iv: [...]}
  let draftRngIv = ENTRY_INTERVALS[0];   // currently edited tab

  // ── Segment-count algorithm ───────────────────────────────────────
  function intervalRatio(cur, nxt) {
    return INTERVAL_MINUTES[cur] / INTERVAL_MINUTES[nxt];
  }
  function computeSegmentCount(curIv, curBars) {
    const nxt = NEXT_INTERVAL[curIv];
    if (!nxt) return null;
    const nNext = curBars * intervalRatio(curIv, nxt);
    return Math.floor(nNext / BARS_PER_SEGMENT_TARGET) + 1;
  }
  function computeSubranges(startIso, endIso, count) {
    const s = new Date(startIso).getTime();
    const e = new Date(endIso).getTime();
    const step = (e - s) / count;
    const subs = [];
    for (let i = 0; i < count; i++) {
      const subS = new Date(s + step * i);
      const subE = new Date(i < count - 1 ? s + step * (i + 1) : e);
      subs.push([subS.toISOString(), subE.toISOString()]);
    }
    return subs;
  }

  // ── Utilities ─────────────────────────────────────────────────────
  function ivLabel(iv) { return i18n[curLang].iv[iv] || iv; }

  function fmtRange(startIso, endIso, interval) {
    const s = new Date(startIso), e = new Date(endIso);
    const pad = n => String(n).padStart(2, '0');
    if (['15m','30m','1h','4h'].includes(interval)) {
      const f = d => `${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
      return `${f(s)} ~ ${f(e)}`;
    }
    const f = d => `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
    return `${f(s)} ~ ${f(e)}`;
  }

  function isoToBinanceStr(iso) {
    const d = new Date(iso);
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const pad = n => String(n).padStart(2, '0');
    return `${d.getDate()} ${months[d.getMonth()]}, ${d.getFullYear()} `
         + `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  function binanceStrToIso(bstr) {
    return new Date(bstr).toISOString();
  }

  function showView(id) {
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.getElementById(id).classList.add('active');
  }

  // ── View 1 ────────────────────────────────────────────────────────
  function buildSymbolPicker() {
    const grid = document.getElementById('symbol-grid');
    grid.innerHTML = '';
    if (SYMBOLS.length === 0) {
      grid.innerHTML = `<div class="empty-hint">${i18n[curLang].no_symbols}</div>`;
      return;
    }
    if (!SYMBOLS.includes(selSymbol)) selSymbol = SYMBOLS[0];
    SYMBOLS.forEach(sym => {
      const short = sym.replace('USDT','');
      const el = document.createElement('div');
      el.className = 'opt' + (sym === selSymbol ? ' active' : '');
      el.textContent = short;
      el.onclick = () => { selSymbol = sym; buildSymbolPicker(); };
      grid.appendChild(el);
    });
  }

  function buildIntervalPicker() {
    const grid = document.getElementById('interval-grid');
    grid.innerHTML = '';
    ENTRY_INTERVALS.forEach(iv => {
      const el = document.createElement('div');
      el.className = 'opt' + (iv === selInterval ? ' active' : '');
      el.textContent = ivLabel(iv);
      el.onclick = () => {
        selInterval = iv;
        buildIntervalPicker();
        buildRangePicker();        // interval changed → range list rebuilds
      };
      grid.appendChild(el);
    });
  }

  function buildRangePicker() {
    const list = document.getElementById('range-list');
    list.innerHTML = '';
    const rngs = RANGES_BY_IV[selInterval] || [];
    if (rngs.length === 0) {
      list.innerHTML = `<div class="empty-hint">${i18n[curLang].no_ranges}</div>`;
      return;
    }
    let idx = selRngIdxByIv[selInterval];
    if (idx >= rngs.length) idx = rngs.length - 1;
    if (idx < 0) idx = 0;
    selRngIdxByIv[selInterval] = idx;
    rngs.forEach((r, i) => {
      const el = document.createElement('div');
      el.className = 'range-item' + (i === idx ? ' active' : '');
      el.textContent = r.label;
      el.onclick = () => { selRngIdxByIv[selInterval] = i; buildRangePicker(); };
      list.appendChild(el);
    });
  }

  async function enterChart() {
    const rngs = RANGES_BY_IV[selInterval] || [];
    if (rngs.length === 0 || SYMBOLS.length === 0) return;
    const r = rngs[selRngIdxByIv[selInterval]];
    stack = [];
    await pushAndRender(
      selSymbol, selInterval,
      binanceStrToIso(r.start),
      binanceStrToIso(r.end),
    );
  }

  // ── View 2 ────────────────────────────────────────────────────────
  async function pushAndRender(symbol, interval, startIso, endIso) {
    showView('view-chart');
    const status  = document.getElementById('status-2');
    const spinner = document.getElementById('spinner-2');
    const img     = document.getElementById('chart-img');
    const tx      = i18n[curLang];

    status.className = '';
    status.textContent = tx.generating;
    spinner.style.display = 'block';
    img.style.display = 'none';
    document.getElementById('sub-section').style.display = 'none';

    try {
      const resp = await fetch('/generate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          symbol, interval,
          start: isoToBinanceStr(startIso),
          end:   isoToBinanceStr(endIso),
        })
      });
      const data = await resp.json();
      if (!data.ok) throw new Error(data.error);

      stack.push({
        symbol, interval,
        start: data.actual_start,
        end:   data.actual_end,
        bars:  data.bars,
      });

      img.src = 'data:image/png;base64,' + data.img;
      img.style.display = 'block';
      status.textContent = tx.done(data.bars);
      status.className = 'ok';

      buildCrumbs();
      buildSubGrid();
    } catch(e) {
      status.textContent = tx.error + ': ' + (e.message || e);
      status.className = 'err';
    } finally {
      spinner.style.display = 'none';
    }
  }

  function buildCrumbs() {
    const el = document.getElementById('crumbs');
    el.innerHTML = '';
    stack.forEach((node, i) => {
      const isLast = (i === stack.length - 1);
      const c = document.createElement('div');
      c.className = 'crumb' + (isLast ? ' current' : '');
      const short = node.symbol.replace('USDT','');
      c.textContent = `${short} ${ivLabel(node.interval)} · ${fmtRange(node.start, node.end, node.interval)}`;
      if (!isLast) c.onclick = () => popTo(i);
      el.appendChild(c);
      if (!isLast) {
        const sep = document.createElement('div');
        sep.className = 'crumb-sep';
        sep.textContent = '▸';
        el.appendChild(sep);
      }
    });
  }

  function buildSubGrid() {
    const top = stack[stack.length - 1];
    const nextIv = NEXT_INTERVAL[top.interval];
    const section = document.getElementById('sub-section');
    const grid    = document.getElementById('sub-grid');

    if (!nextIv) {
      section.style.display = 'block';
      document.getElementById('sub-hint').textContent = i18n[curLang].end_msg;
      grid.innerHTML = '';
      return;
    }

    const segCount = computeSegmentCount(top.interval, top.bars);
    const subs = computeSubranges(top.start, top.end, segCount);
    const estBars = Math.round(top.bars * INTERVAL_MINUTES[top.interval]
                                       / INTERVAL_MINUTES[nextIv] / segCount);

    section.style.display = 'block';
    document.getElementById('sub-hint').textContent =
      i18n[curLang].drill_hint(estBars);

    grid.className = 'grid subs' +
      (segCount >= 5 ? ' cols3' : (segCount >= 3 ? ' cols2' : ''));

    grid.innerHTML = '';
    subs.forEach(([s, e], idx) => {
      const el = document.createElement('div');
      el.className = 'sub-card';
      el.innerHTML = `<div class="sub-card-head">${idx+1}/${segCount} · ${ivLabel(nextIv)}</div>`
                   + `<div class="sub-card-body">${fmtRange(s, e, nextIv)}</div>`;
      el.onclick = () => pushAndRender(top.symbol, nextIv, s, e);
      grid.appendChild(el);
    });
  }

  function popTo(idx) {
    const target = stack[idx];
    stack = stack.slice(0, idx);
    pushAndRender(target.symbol, target.interval, target.start, target.end);
  }

  function resetToStart() {
    stack = [];
    showView('view-select');
  }

  // ── View 3: settings ──────────────────────────────────────────────
  function openSettings() {
    prevView = document.querySelector('.view.active').id;
    if (prevView === 'view-settings') prevView = 'view-select';

    fetch('/settings').then(r => r.json()).then(data => {
      draftLang = data.language;
      draftSyms = [...data.symbols];
      draftRngs = {};
      ENTRY_INTERVALS.forEach(iv => {
        draftRngs[iv] = (data.ranges[iv] || []).map(r => ({...r}));
      });
      draftRngIv = ENTRY_INTERVALS[0];
      renderSettings();
      showView('view-settings');
    }).catch(() => {
      draftLang = curLang;
      draftSyms = [...SYMBOLS];
      draftRngs = {};
      ENTRY_INTERVALS.forEach(iv => {
        draftRngs[iv] = (RANGES_BY_IV[iv] || []).map(r => ({...r}));
      });
      draftRngIv = ENTRY_INTERVALS[0];
      renderSettings();
      showView('view-settings');
    });
  }

  function setSettingsLang(code) {
    draftLang = code;
    renderSettings();
  }

  function setDraftRngIv(iv) {
    draftRngIv = iv;
    renderSettings();
  }

  function renderSettings() {
    const tx = i18n[draftLang];
    document.getElementById('lbl-set-language').textContent = tx.set_language;
    document.getElementById('lbl-set-symbols').textContent  = tx.set_symbols;
    document.getElementById('lbl-set-ranges').textContent   = tx.set_ranges;
    document.getElementById('btn-set-save').textContent     = tx.set_save;
    document.getElementById('btn-set-cancel').textContent   = tx.set_cancel;
    document.getElementById('btn-set-sym-add').textContent  = tx.set_add;
    document.getElementById('btn-set-rng-add').textContent  = tx.set_add;
    document.getElementById('set-rng-hint').textContent     = tx.set_hint;
    document.getElementById('set-sym-input').placeholder    = tx.set_sym_ph;
    document.getElementById('set-rng-label').placeholder    = tx.set_lbl_ph;
    document.getElementById('set-rng-start').placeholder    = tx.set_start_ph;
    document.getElementById('set-rng-end').placeholder      = tx.set_end_ph;

    document.getElementById('set-lang-en').classList.toggle('active', draftLang==='en');
    document.getElementById('set-lang-zh').classList.toggle('active', draftLang==='zh');

    // Entry-interval tabs
    const tabs = document.getElementById('set-iv-tabs');
    tabs.innerHTML = '';
    ENTRY_INTERVALS.forEach(iv => {
      const el = document.createElement('div');
      el.className = 'opt' + (iv === draftRngIv ? ' active' : '');
      el.textContent = tx.iv[iv] || iv;
      el.onclick = () => setDraftRngIv(iv);
      tabs.appendChild(el);
    });

    // Symbols list
    const symsList = document.getElementById('set-syms-list');
    symsList.innerHTML = '';
    draftSyms.forEach((s, idx) => {
      const row = document.createElement('div');
      row.className = 'settings-row';
      row.innerHTML = `<div class="row-text">${s}</div>`;
      const btn = document.createElement('button');
      btn.className = 'del-btn';
      btn.textContent = '✕';
      btn.onclick = () => { draftSyms.splice(idx, 1); renderSettings(); };
      row.appendChild(btn);
      symsList.appendChild(row);
    });

    // Range list for the current tab
    const rngsList = document.getElementById('set-rngs-list');
    rngsList.innerHTML = '';
    (draftRngs[draftRngIv] || []).forEach((r, idx) => {
      const row = document.createElement('div');
      row.className = 'settings-row';
      row.innerHTML = `<div class="row-text">${r.label}<small>${r.start}  →  ${r.end}</small></div>`;
      const btn = document.createElement('button');
      btn.className = 'del-btn';
      btn.textContent = '✕';
      btn.onclick = () => { draftRngs[draftRngIv].splice(idx, 1); renderSettings(); };
      row.appendChild(btn);
      rngsList.appendChild(row);
    });

    document.getElementById('status-set').textContent = '';
    document.getElementById('status-set').className = '';
  }

  function addSettingSym() {
    const input = document.getElementById('set-sym-input');
    const s = input.value.trim().toUpperCase();
    if (!s) return;
    const tx = i18n[draftLang];
    if (draftSyms.includes(s)) { flashSetStatus(tx.sym_dup, 'err'); return; }
    draftSyms.push(s);
    input.value = '';
    renderSettings();
  }

  const _MONTHS = {'Jan':0,'Feb':1,'Mar':2,'Apr':3,'May':4,'Jun':5,
                   'Jul':6,'Aug':7,'Sep':8,'Oct':9,'Nov':10,'Dec':11};
  function parseBinanceDate(s) {
    const m = s.trim().match(/^(\d{1,2})\s+([A-Za-z]{3}),\s+(\d{4})$/);
    if (!m) return null;
    const day = parseInt(m[1]), mon = _MONTHS[m[2]], year = parseInt(m[3]);
    if (mon === undefined) return null;
    const d = new Date(Date.UTC(year, mon, day));
    if (d.getUTCDate() !== day || d.getUTCMonth() !== mon || d.getUTCFullYear() !== year) return null;
    return d;
  }

  function addSettingRng() {
    const lbl = document.getElementById('set-rng-label').value.trim();
    const st  = document.getElementById('set-rng-start').value.trim();
    const en  = document.getElementById('set-rng-end').value.trim();
    const tx = i18n[draftLang];
    if (!lbl || !st || !en) {
      flashSetStatus(tx.rng_empty, 'err');
      return;
    }
    const sd = parseBinanceDate(st), ed = parseBinanceDate(en);
    if (!sd || !ed) {
      flashSetStatus(tx.date_bad, 'err');
      return;
    }
    if (ed <= sd) {
      flashSetStatus(tx.date_order, 'err');
      return;
    }
    draftRngs[draftRngIv].push({label: lbl, start: st, end: en});
    document.getElementById('set-rng-label').value = '';
    document.getElementById('set-rng-start').value = '';
    document.getElementById('set-rng-end').value = '';
    renderSettings();
  }

  function flashSetStatus(msg, cls) {
    const el = document.getElementById('status-set');
    el.textContent = msg;
    el.className = cls || '';
  }

  function cancelSettings() {
    showView(prevView);
  }

  async function saveSettings() {
    const tx = i18n[draftLang];
    if (draftSyms.length === 0) {
      flashSetStatus(tx.sym_min, 'err');
      return;
    }
    // Each entry interval needs at least one range
    for (const iv of ENTRY_INTERVALS) {
      if (!draftRngs[iv] || draftRngs[iv].length === 0) {
        draftRngIv = iv;          // jump to that tab so the user can see
        renderSettings();
        flashSetStatus(tx.rng_min(tx.iv[iv] || iv), 'err');
        return;
      }
    }
    try {
      const resp = await fetch('/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          language: draftLang,
          symbols:  draftSyms,
          ranges:   draftRngs,
        })
      });
      const data = await resp.json();
      if (!data.ok) {
        flashSetStatus(tx.set_save_err + ': ' + data.error, 'err');
        return;
      }
      // Apply to runtime state
      SYMBOLS = data.settings.symbols;
      RANGES_BY_IV = data.settings.ranges;
      switchLang(data.settings.language, false);
      buildSymbolPicker();
      buildIntervalPicker();
      buildRangePicker();
      flashSetStatus(tx.set_saved, 'ok');
      setTimeout(() => showView(prevView), 600);
    } catch (e) {
      flashSetStatus(tx.set_save_err + ': ' + (e.message || e), 'err');
    }
  }

  // ── Language switch (top-bar quick switch) ────────────────────────
  async function switchLang(lang, persist) {
    curLang = lang;
    const tx = i18n[lang];
    document.getElementById('title').textContent          = tx.title;
    document.getElementById('lbl-symbol').textContent     = tx.symbol;
    document.getElementById('lbl-interval').textContent   = tx.interval;
    document.getElementById('lbl-range').textContent      = tx.timerange;
    document.getElementById('gen-btn').textContent        = tx.generate;
    document.getElementById('btn-reset-text').textContent = tx.reset;
    document.getElementById('btn-en').classList.toggle('active', lang==='en');
    document.getElementById('btn-zh').classList.toggle('active', lang==='zh');
    // Entry-interval button text follows the language
    buildIntervalPicker();
    if (stack.length > 0) {
      buildCrumbs();
      buildSubGrid();
    }
    // Empty-list placeholder text follows the language too
    if (SYMBOLS.length === 0) buildSymbolPicker();
    buildRangePicker();
    if (persist) {
      try {
        const cur = await fetch('/settings').then(r => r.json());
        await fetch('/settings', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({...cur, language: lang}),
        });
      } catch (e) { /* silent */ }
    }
  }

  // ── Init ──────────────────────────────────────────────────────────
  buildSymbolPicker();
  buildIntervalPicker();
  buildRangePicker();
  switchLang(curLang, false);
</script>
</body>
</html>
"""


@app.route('/')
def index():
    cur = settings.load_settings()
    return render_template_string(
        HTML,
        symbols_json          = json.dumps(cur['symbols']),
        ranges_json           = json.dumps(cur['ranges']),
        entry_intervals_json  = json.dumps(list(ENTRY_INTERVALS)),
        language_json         = json.dumps(cur['language']),
        next_interval_json    = json.dumps(NEXT_INTERVAL),
        interval_minutes_json = json.dumps(INTERVAL_MINUTES),
        target_json           = json.dumps(BARS_PER_SEGMENT_TARGET),
    )


@app.route('/generate', methods=['POST'])
def generate():
    data = request.get_json()
    symbol   = data.get('symbol',   'BTCUSDT')
    interval = data.get('interval', 'weekly')
    start    = data.get('start')
    end      = data.get('end')
    try:
        img_b64, actual_start, actual_end, bars = generate_chart(
            symbol, interval, start, end
        )
        return jsonify({
            'ok': True,
            'img': img_b64,
            'actual_start': actual_start,
            'actual_end':   actual_end,
            'bars':         bars,
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


# ── Settings endpoints ───────────────────────────────────────────────
@app.route('/settings', methods=['GET'])
def get_settings():
    return jsonify(settings.load_settings())


@app.route('/settings', methods=['POST'])
def post_settings():
    """
    Validate and save settings. Request body:
        {
          language: 'en'|'zh',
          symbols: [...],
          ranges:  { weekly:[...], 3day:[...], daily:[...] }
        }
    Any invalid field rejects the entire payload.
    """
    data = request.get_json() or {}

    language = data.get('language')
    if language not in ('en', 'zh'):
        return jsonify({'ok': False, 'error': 'invalid language'})

    syms = data.get('symbols')
    if not isinstance(syms, list) or not syms:
        return jsonify({'ok': False, 'error': 'symbols must be a non-empty list'})
    clean_syms = []
    for s in syms:
        if not isinstance(s, str) or not s.strip():
            return jsonify({'ok': False, 'error': 'invalid symbol entry'})
        clean_syms.append(s.strip().upper())

    rngs = data.get('ranges')
    if not isinstance(rngs, dict):
        return jsonify({'ok': False, 'error': 'ranges must be a dict keyed by interval'})

    clean_rngs = {}
    for iv in ENTRY_INTERVALS:
        rl = rngs.get(iv)
        if not isinstance(rl, list) or not rl:
            return jsonify({'ok': False,
                            'error': f"ranges['{iv}'] must be a non-empty list"})
        clean_list = []
        for r in rl:
            if not isinstance(r, dict):
                return jsonify({'ok': False,
                                'error': f"invalid range entry in '{iv}'"})
            label = (r.get('label') or '').strip()
            start = (r.get('start') or '').strip()
            end_  = (r.get('end')   or '').strip()
            if not (label and start and end_):
                return jsonify({'ok': False,
                                'error': f"range entry in '{iv}' missing fields"})
            try:
                s_dt = settings.validate_date_str(start)
                e_dt = settings.validate_date_str(end_)
            except Exception:
                return jsonify({'ok': False,
                                'error': f"invalid date format in '{iv}/{label}'"})
            if e_dt <= s_dt:
                return jsonify({'ok': False,
                                'error': f"end must be after start in '{iv}/{label}'"})
            clean_list.append({'label': label, 'start': start, 'end': end_})
        clean_rngs[iv] = clean_list

    new_settings = {
        'language': language,
        'symbols':  clean_syms,
        'ranges':   clean_rngs,
    }
    try:
        settings.save_settings(new_settings)
    except Exception as e:
        return jsonify({'ok': False, 'error': f'save failed: {e}'})

    return jsonify({'ok': True, 'settings': new_settings})


if __name__ == '__main__':
    print("Starting server: http://0.0.0.0:5000")
    print("Mobile access:   http://<your-LAN-IP>:5000")
    app.run(host='0.0.0.0', port=5000, debug=False)
