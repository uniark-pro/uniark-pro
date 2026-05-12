"""
K 线分析 - Web App（含设置面板，多 market 版）
================================================
入口：选 market → 选标的 → 选入口周期 → 选时段 → 看图
钻取：根据当前图的 K 线根数动态计算段数 = floor(N_next/385)+1
后端 /generate 返回图、实际窗口、根数。

market：
  顶部条加了一个 market toggle（虚拟币/美股/A股/港股）。切换会重置 symbol/range
  选择区到该 market 的 settings 数据。每个 market 维护各自独立的
  symbols 和 ranges，互不干扰。

入口周期（按 market）：
  - crypto                          : weekly / 3day / daily
  - us_stock / cn_stock / hk_stock  : weekly / daily

钻取金字塔（按 market）：
  - crypto                          : weekly→3day→daily→4h→1h→30m→15m
  - us_stock / cn_stock / hk_stock  : weekly→daily→1h→(终止)

设置：
  顶栏 ⚙ 按钮打开设置视图。设置里有 market tab，下面的 symbols 和
  ranges 区按当前 market tab 显示。改动通过 /settings POST 写入
  user_settings.json，与 main.py 共享。
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

from plot_kline import render_chart
from navigation import (NEXT_INTERVAL_BY_MARKET, INTERVAL_MINUTES,
                        BARS_PER_SEGMENT_TARGET)
import settings
from settings import MARKETS, ENTRY_INTERVALS_BY_MARKET, get_entry_intervals

app = Flask(__name__)


def generate_chart(market, symbol, interval, start_str, end_str):
    """渲染并返回 (b64, actual_start_iso, actual_end_iso, bars_count)"""
    fig, df, _ = render_chart(market, symbol, interval,
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
    .grid.markets   { grid-template-columns: repeat(4, 1fr); }
    .grid.symbols   { grid-template-columns: repeat(auto-fit, minmax(80px, 1fr)); }
    .grid.intervals { grid-template-columns: repeat(3, 1fr); }
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

    /* 钻取 */
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

    /* ── 设置视图 ───────────────────────────────────────── */
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

    .mk-tabs { display: grid; grid-template-columns: repeat(4, 1fr);
               gap: 6px; margin-bottom: 8px; }
    .mk-tabs .opt { padding: 9px 8px; }

    .iv-tabs { display: grid; gap: 6px; margin-bottom: 8px; }
    .iv-tabs.cols2 { grid-template-columns: repeat(2, 1fr); }
    .iv-tabs.cols3 { grid-template-columns: repeat(3, 1fr); }
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

  <!-- ═══════ 视图 1: 选 market + 标的 + 入口周期 + 时段 ═══════════════ -->
  <div id="view-select" class="view active">
    <div class="section-title" id="lbl-market">Market</div>
    <div class="grid markets" id="market-grid"></div>

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

  <!-- ═══════ 视图 2: 图 + 钻取 ════════════════════════════════════════ -->
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

  <!-- ═══════ 视图 3: 设置 ═════════════════════════════════════════════ -->
  <div id="view-settings" class="view">
    <div class="section-title" id="lbl-set-language">Language</div>
    <div class="lang-row">
      <div class="opt" id="set-lang-en" onclick="setSettingsLang('en')">EN</div>
      <div class="opt" id="set-lang-zh" onclick="setSettingsLang('zh')">中文</div>
    </div>

    <div class="section-title" id="lbl-set-market">Market</div>
    <div class="mk-tabs" id="set-mk-tabs"></div>

    <div class="section-title" id="lbl-set-symbols">Symbols</div>
    <div id="set-syms-list"></div>
    <div class="add-form">
      <input type="text" class="input" id="set-sym-input"
             placeholder="Symbol (e.g. BTCUSDT)">
      <input type="text" class="input" id="set-sym-cn-input"
             placeholder="Name (optional)">
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
  // ── 服务端注入 ─────────────────────────────────────────────────────
  const MARKETS                    = {{ markets_json | safe }};
  // ENTRY_INTERVALS_BY_MK: { crypto: ['weekly','3day','daily'], us_stock: ['weekly','daily'], ... }
  const ENTRY_INTERVALS_BY_MK      = {{ entry_intervals_by_mk_json | safe }};
  // NEXT_INTERVAL_BY_MK: { crypto: {weekly:'3day',...}, us_stock: {weekly:'daily', daily:'1h', '1h':null}, ... }
  const NEXT_INTERVAL_BY_MK        = {{ next_interval_by_mk_json | safe }};
  const INTERVAL_MINUTES           = {{ interval_minutes_json | safe }};
  const BARS_PER_SEGMENT_TARGET    = {{ target_json | safe }};
  let curLang                      = {{ language_json | safe }};
  let selMarket                    = {{ market_json | safe }};
  // 每个 market 各自的 symbols + ranges_by_iv
  // SYMBOLS_BY_MK: { crypto: [...], us_stock: [...], cn_stock: [...], hk_stock: [...] }
  // RANGES_BY_MK_IV: { crypto: {weekly:[...],...}, us_stock: {weekly:[...],...}, ... }
  let SYMBOLS_BY_MK                = {{ symbols_by_mk_json | safe }};
  let RANGES_BY_MK_IV              = {{ ranges_by_mk_iv_json | safe }};
  // SYMBOL_CONFIG_BY_MK: { mk: { ticker: {short, cn_name}, ... } }
  // 拿来给按钮决定显示中文（'茅台'）还是英文短码（'600519'），按 curLang 切换。
  // 表里没登记的 ticker 会走前端 fallback：去后缀。
  const SYMBOL_CONFIG_BY_MK        = {{ symbol_config_by_mk_json | safe }};
  // 用户在 settings 里给标的设置的中文名（v4 新增），结构 { mk: { ticker: cn_name } }。
  // 优先级最高：shortFor 先查这个，再查内置 SYMBOL_CONFIG_BY_MK，最后 fallback。
  // 设置保存后由 reloadSettings() 同步刷新这份字典。
  let USER_CN_NAMES_BY_MK          = {{ user_cn_names_by_mk_json | safe }};

  // ── i18n ──────────────────────────────────────────────────────────
  const i18n = {
    en: {
      title:      '📈 K-Line Generator',
      market:     'MARKET',
      symbol:     'SYMBOL',
      interval:   'INTERVAL',
      timerange:  'TIME RANGE',
      generate:   'Generate Chart',
      drill_hint: bars => `Drill into ${bars}-bar sub-segments:`,
      reset:      '↺ Start over',
      generating: 'Generating...',
      done:       bars => `Done ✓ (${bars} bars)`,
      error:      'Error ✗',
      end_msg:    '(End of pyramid — no further drill-down)',
      iv: { '15m':'15m','30m':'30m','1h':'1h','4h':'4h',
            'daily':'Daily','3day':'3-Day','weekly':'Weekly' },
      mk: { 'crypto': 'Crypto',
            'us_stock': 'US Stocks',
            'cn_stock': 'A-Shares',
            'hk_stock': 'HK Stocks' },
      // settings
      set_language: 'LANGUAGE',
      set_market:   'MARKET',
      set_symbols:  'SYMBOLS',
      set_ranges:   'TIME RANGES',
      set_save:     'Save',
      set_cancel:   'Cancel',
      set_add:      'Add',
      set_sym_ph_crypto:   'Symbol (e.g. BTCUSDT)',
      set_sym_ph_us_stock: 'Ticker (e.g. MU, NVDA, AAPL)',
      set_sym_ph_cn_stock: 'Ticker (e.g. 600519.SS, 000001.SZ)',
      set_sym_ph_hk_stock: 'Ticker (e.g. 0700.HK, 9988.HK)',
      set_sym_ph_cn:       'Name (optional)',
      set_lbl_ph:   'Label (e.g. 2024-01 ~ 2024-12)',
      set_start_ph: 'Start date (e.g. 17 Aug, 2017)',
      set_end_ph:   'End date (e.g. 30 Dec, 2022)',
      set_hint:     "Date format: 'DD Mon, YYYY'  e.g. '17 Aug, 2017'",
      set_saved:    'Settings saved ✓',
      set_save_err: 'Save failed',
      sym_dup:      'Symbol already exists',
      sym_min:      mk => `${mk}: at least one symbol is required`,
      rng_min:      (mk, iv) => `${mk} · ${iv}: at least one time range is required`,
      rng_empty:    'Please fill all three fields',
      date_bad:     "Invalid date format. Use 'DD Mon, YYYY'.",
      date_order:   'End date must be after start date',
      no_symbols:   'No symbols configured.',
      no_ranges:    'No time ranges for this interval.',
    },
    zh: {
      title:      '📈 K线生成器',
      market:     '市场',
      symbol:     '标的',
      interval:   '周期',
      timerange:  '时间段',
      generate:   '生成图表',
      drill_hint: bars => `点击进入下一级（每段约 ${bars} 根 K 线）：`,
      reset:      '↺ 重新开始',
      generating: '正在生成...',
      done:       bars => `完成 ✓ (${bars} 根)`,
      error:      '错误 ✗',
      end_msg:    '(已到金字塔末端 — 下方无更细周期)',
      iv: { '15m':'15分钟','30m':'30分钟','1h':'1小时','4h':'4小时',
            'daily':'日线','3day':'3日线','weekly':'周线' },
      mk: { 'crypto': '虚拟币',
            'us_stock': '美股',
            'cn_stock': 'A股',
            'hk_stock': '港股' },
      // settings
      set_language: '语言',
      set_market:   '市场',
      set_symbols:  '标的',
      set_ranges:   '时间段',
      set_save:     '保存',
      set_cancel:   '取消',
      set_add:      '添加',
      set_sym_ph_crypto:   '币种（例 BTCUSDT）',
      set_sym_ph_us_stock: '美股代码（例 MU、NVDA、AAPL）',
      set_sym_ph_cn_stock: 'A股代码（例 600519.SS、000001.SZ）',
      set_sym_ph_hk_stock: '港股代码（例 0700.HK、9988.HK）',
      set_sym_ph_cn:       '中文名（选填）',
      set_lbl_ph:   '标签（例 2024-01 ~ 2024-12）',
      set_start_ph: '起始日期（例 17 Aug, 2017）',
      set_end_ph:   '结束日期（例 30 Dec, 2022）',
      set_hint:     "日期格式：'DD Mon, YYYY'  例如 '17 Aug, 2017'",
      set_saved:    '设置已保存 ✓',
      set_save_err: '保存失败',
      sym_dup:      '该标的已存在',
      sym_min:      mk => `${mk}：至少需要保留一个标的`,
      rng_min:      (mk, iv) => `${mk} · ${iv}：该周期至少需要保留一个时间段`,
      rng_empty:    '请填写所有三个字段',
      date_bad:     "日期格式不正确。请使用 'DD Mon, YYYY' 格式。",
      date_order:   '结束日期必须晚于起始日期',
      no_symbols:   '未配置标的。',
      no_ranges:    '该周期未配置时间段。',
    }
  };

  // ── 状态 ──────────────────────────────────────────────────────────
  // 每个 market 独立维护选中的 symbol / interval / range 下标
  let selSymbolByMk = {};
  let selIntervalByMk = {};
  let selRngIdxByMkIv = {};
  MARKETS.forEach(mk => {
    const syms = SYMBOLS_BY_MK[mk] || [];
    selSymbolByMk[mk] = syms[0] || null;
    selIntervalByMk[mk] = ENTRY_INTERVALS_BY_MK[mk][0];
    selRngIdxByMkIv[mk] = {};
    ENTRY_INTERVALS_BY_MK[mk].forEach(iv => {
      const rs = (RANGES_BY_MK_IV[mk] && RANGES_BY_MK_IV[mk][iv]) || [];
      selRngIdxByMkIv[mk][iv] = Math.max(0, rs.length - 1);
    });
  });

  let stack = [];          // 导航栈，每个 node 含 market
  let prevView = 'view-select';

  // 设置面板的临时草稿（取消时丢弃）
  let draftLang = curLang;
  let draftMk   = selMarket;       // 设置里当前编辑哪个 market
  let draftSymsByMk = {};          // {mk: [...]}
  let draftRngsByMkIv = {};        // {mk: {iv: [...]}}
  let draftRngIvByMk = {};         // {mk: '当前 tab'}

  // ── 工具 ──────────────────────────────────────────────────────────
  function shortFor(mk, sym) {
    // 优先级（高到低）：
    //   1) USER_CN_NAMES_BY_MK 用户在 settings 里设的（仅 zh 用）
    //   2) SYMBOL_CONFIG_BY_MK 内置表（茅台/腾讯/美光等）
    //   3) fallback：crypto 截 USDT，A 股/港股去 .SS/.SZ/.HK
    if (curLang === 'zh') {
      const userCn = (USER_CN_NAMES_BY_MK[mk] || {})[sym];
      if (userCn) return userCn;
    }
    const cfg = (SYMBOL_CONFIG_BY_MK[mk] || {})[sym];
    if (cfg) {
      return curLang === 'zh' ? cfg.cn_name : cfg.short;
    }
    if (mk === 'crypto' && sym.endsWith('USDT')) return sym.slice(0, -4);
    if (mk === 'cn_stock' || mk === 'hk_stock') {
      for (const suffix of ['.SS', '.SZ', '.HK']) {
        if (sym.endsWith(suffix)) return sym.slice(0, -suffix.length);
      }
    }
    return sym;
  }
  function ivLabel(iv) { return i18n[curLang].iv[iv] || iv; }
  function mkLabel(mk) { return i18n[curLang].mk[mk] || mk; }
  function curSyms()   { return SYMBOLS_BY_MK[selMarket] || []; }
  function curEntryIvs() { return ENTRY_INTERVALS_BY_MK[selMarket]; }
  function curIv()     { return selIntervalByMk[selMarket]; }
  function curRngs()   { return (RANGES_BY_MK_IV[selMarket] || {})[curIv()] || []; }

  // ── 段数算法（按 market 走对应金字塔）─────────────────────────────
  function intervalRatio(cur, nxt) {
    return INTERVAL_MINUTES[cur] / INTERVAL_MINUTES[nxt];
  }
  function nextIntervalOf(market, iv) {
    const m = NEXT_INTERVAL_BY_MK[market] || {};
    return m[iv] || null;
  }
  function computeSegmentCount(market, curIvName, curBars) {
    const nxt = nextIntervalOf(market, curIvName);
    if (!nxt) return null;
    const nNext = curBars * intervalRatio(curIvName, nxt);
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

  // ── 视图 1 ────────────────────────────────────────────────────────
  function buildMarketPicker() {
    const grid = document.getElementById('market-grid');
    grid.innerHTML = '';
    MARKETS.forEach(mk => {
      const el = document.createElement('div');
      el.className = 'opt' + (mk === selMarket ? ' active' : '');
      el.textContent = mkLabel(mk);
      el.onclick = () => {
        if (mk === selMarket) return;
        selMarket = mk;
        buildMarketPicker();
        buildSymbolPicker();
        buildIntervalPicker();
        buildRangePicker();
      };
      grid.appendChild(el);
    });
  }

  function buildSymbolPicker() {
    const grid = document.getElementById('symbol-grid');
    grid.innerHTML = '';
    const syms = curSyms();
    if (syms.length === 0) {
      grid.innerHTML = `<div class="empty-hint">${i18n[curLang].no_symbols}</div>`;
      selSymbolByMk[selMarket] = null;
      return;
    }
    if (!syms.includes(selSymbolByMk[selMarket])) {
      selSymbolByMk[selMarket] = syms[0];
    }
    syms.forEach(sym => {
      const short = shortFor(selMarket, sym);
      const el = document.createElement('div');
      el.className = 'opt' + (sym === selSymbolByMk[selMarket] ? ' active' : '');
      el.textContent = short;
      el.onclick = () => { selSymbolByMk[selMarket] = sym; buildSymbolPicker(); };
      grid.appendChild(el);
    });
  }

  function buildIntervalPicker() {
    const grid = document.getElementById('interval-grid');
    grid.innerHTML = '';
    const ivs = curEntryIvs();
    // 校正当前 market 选中的 iv
    if (!ivs.includes(selIntervalByMk[selMarket])) {
      selIntervalByMk[selMarket] = ivs[0];
    }
    // 列数适配（stock 只有 2 列）
    grid.style.gridTemplateColumns = `repeat(${ivs.length}, 1fr)`;
    ivs.forEach(iv => {
      const el = document.createElement('div');
      el.className = 'opt' + (iv === selIntervalByMk[selMarket] ? ' active' : '');
      el.textContent = ivLabel(iv);
      el.onclick = () => {
        selIntervalByMk[selMarket] = iv;
        buildIntervalPicker();
        buildRangePicker();
      };
      grid.appendChild(el);
    });
  }

  function buildRangePicker() {
    const list = document.getElementById('range-list');
    list.innerHTML = '';
    const rngs = curRngs();
    if (rngs.length === 0) {
      list.innerHTML = `<div class="empty-hint">${i18n[curLang].no_ranges}</div>`;
      return;
    }
    let idx = selRngIdxByMkIv[selMarket][curIv()];
    if (idx === undefined || idx >= rngs.length) idx = rngs.length - 1;
    if (idx < 0) idx = 0;
    selRngIdxByMkIv[selMarket][curIv()] = idx;
    rngs.forEach((r, i) => {
      const el = document.createElement('div');
      el.className = 'range-item' + (i === idx ? ' active' : '');
      el.textContent = r.label;
      el.onclick = () => { selRngIdxByMkIv[selMarket][curIv()] = i; buildRangePicker(); };
      list.appendChild(el);
    });
  }

  async function enterChart() {
    const rngs = curRngs();
    const sym = selSymbolByMk[selMarket];
    if (rngs.length === 0 || !sym) return;
    const r = rngs[selRngIdxByMkIv[selMarket][curIv()]];
    stack = [];
    await pushAndRender(
      selMarket, sym, curIv(),
      binanceStrToIso(r.start),
      binanceStrToIso(r.end),
    );
  }

  // ── 视图 2 ────────────────────────────────────────────────────────
  async function pushAndRender(market, symbol, interval, startIso, endIso) {
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
          market, symbol, interval,
          start: isoToBinanceStr(startIso),
          end:   isoToBinanceStr(endIso),
        })
      });
      const data = await resp.json();
      if (!data.ok) throw new Error(data.error);

      stack.push({
        market, symbol, interval,
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
      const short = shortFor(node.market, node.symbol);
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
    const nextIv = nextIntervalOf(top.market, top.interval);
    const section = document.getElementById('sub-section');
    const grid    = document.getElementById('sub-grid');

    if (!nextIv) {
      section.style.display = 'block';
      document.getElementById('sub-hint').textContent = i18n[curLang].end_msg;
      grid.innerHTML = '';
      return;
    }

    const segCount = computeSegmentCount(top.market, top.interval, top.bars);
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
      el.onclick = () => pushAndRender(top.market, top.symbol, nextIv, s, e);
      grid.appendChild(el);
    });
  }

  function popTo(idx) {
    const target = stack[idx];
    stack = stack.slice(0, idx);
    pushAndRender(target.market, target.symbol, target.interval, target.start, target.end);
  }

  function resetToStart() {
    stack = [];
    showView('view-select');
  }

  // ── 视图 3: 设置 ──────────────────────────────────────────────────
  function openSettings() {
    prevView = document.querySelector('.view.active').id;
    if (prevView === 'view-settings') prevView = 'view-select';

    fetch('/settings').then(r => r.json()).then(data => {
      draftLang = data.language;
      draftMk   = (data.market && MARKETS.includes(data.market)) ? data.market : selMarket;
      draftSymsByMk = {};
      draftRngsByMkIv = {};
      draftRngIvByMk = {};
      MARKETS.forEach(mk => {
        const blk = data[mk] || { symbols: [], ranges: {} };
        // v4 dict 数组深拷贝。每个元素是 {ticker, cn_name}。
        // 兼容 v3 字符串元素（虽然后端 load_settings 已经升级了，但稳健点）
        draftSymsByMk[mk] = (blk.symbols || []).map(s =>
          typeof s === 'string' ? {ticker: s, cn_name: ''} : {...s}
        );
        draftRngsByMkIv[mk] = {};
        ENTRY_INTERVALS_BY_MK[mk].forEach(iv => {
          draftRngsByMkIv[mk][iv] = ((blk.ranges || {})[iv] || []).map(r => ({...r}));
        });
        draftRngIvByMk[mk] = ENTRY_INTERVALS_BY_MK[mk][0];
      });
      renderSettings();
      showView('view-settings');
    }).catch(() => {
      // 远端拉失败 → 用当前运行时状态作 draft
      draftLang = curLang;
      draftMk   = selMarket;
      draftSymsByMk = {};
      draftRngsByMkIv = {};
      draftRngIvByMk = {};
      MARKETS.forEach(mk => {
        // 运行时 SYMBOLS_BY_MK 是 ticker 字符串数组，cn_name 从 USER_CN_NAMES_BY_MK 查
        draftSymsByMk[mk] = (SYMBOLS_BY_MK[mk] || []).map(t => ({
          ticker: t,
          cn_name: (USER_CN_NAMES_BY_MK[mk] || {})[t] || ''
        }));
        draftRngsByMkIv[mk] = {};
        ENTRY_INTERVALS_BY_MK[mk].forEach(iv => {
          draftRngsByMkIv[mk][iv] = ((RANGES_BY_MK_IV[mk] || {})[iv] || []).map(r => ({...r}));
        });
        draftRngIvByMk[mk] = ENTRY_INTERVALS_BY_MK[mk][0];
      });
      renderSettings();
      showView('view-settings');
    });
  }

  function setSettingsLang(code) {
    draftLang = code;
    renderSettings();
  }

  function setDraftMk(mk) {
    draftMk = mk;
    renderSettings();
  }

  function setDraftRngIv(iv) {
    draftRngIvByMk[draftMk] = iv;
    renderSettings();
  }

  function symPhKey() {
    // 4 market 各自独立的占位符 key（'set_sym_ph_crypto' / '..._us_stock' / etc）
    return 'set_sym_ph_' + draftMk;
  }

  function renderSettings() {
    const tx = i18n[draftLang];
    document.getElementById('lbl-set-language').textContent = tx.set_language;
    document.getElementById('lbl-set-market').textContent   = tx.set_market;
    document.getElementById('lbl-set-symbols').textContent  = tx.set_symbols;
    document.getElementById('lbl-set-ranges').textContent   = tx.set_ranges;
    document.getElementById('btn-set-save').textContent     = tx.set_save;
    document.getElementById('btn-set-cancel').textContent   = tx.set_cancel;
    document.getElementById('btn-set-sym-add').textContent  = tx.set_add;
    document.getElementById('btn-set-rng-add').textContent  = tx.set_add;
    document.getElementById('set-rng-hint').textContent     = tx.set_hint;
    document.getElementById('set-sym-input').placeholder    = tx[symPhKey()];
    document.getElementById('set-sym-cn-input').placeholder = tx.set_sym_ph_cn;
    document.getElementById('set-rng-label').placeholder    = tx.set_lbl_ph;
    document.getElementById('set-rng-start').placeholder    = tx.set_start_ph;
    document.getElementById('set-rng-end').placeholder      = tx.set_end_ph;

    document.getElementById('set-lang-en').classList.toggle('active', draftLang==='en');
    document.getElementById('set-lang-zh').classList.toggle('active', draftLang==='zh');

    // market tab
    const mkTabs = document.getElementById('set-mk-tabs');
    mkTabs.innerHTML = '';
    MARKETS.forEach(mk => {
      const el = document.createElement('div');
      el.className = 'opt' + (mk === draftMk ? ' active' : '');
      el.textContent = tx.mk[mk] || mk;
      el.onclick = () => setDraftMk(mk);
      mkTabs.appendChild(el);
    });

    // 入口周期 tab（按当前 draftMk 走）
    const ivs = ENTRY_INTERVALS_BY_MK[draftMk];
    const tabs = document.getElementById('set-iv-tabs');
    tabs.innerHTML = '';
    tabs.className = 'iv-tabs ' + (ivs.length >= 3 ? 'cols3' : 'cols2');
    if (!ivs.includes(draftRngIvByMk[draftMk])) {
      draftRngIvByMk[draftMk] = ivs[0];
    }
    ivs.forEach(iv => {
      const el = document.createElement('div');
      el.className = 'opt' + (iv === draftRngIvByMk[draftMk] ? ' active' : '');
      el.textContent = tx.iv[iv] || iv;
      el.onclick = () => setDraftRngIv(iv);
      tabs.appendChild(el);
    });

    // 标的列表（当前 draftMk 的，v4 dict 数组）
    const symsList = document.getElementById('set-syms-list');
    symsList.innerHTML = '';
    (draftSymsByMk[draftMk] || []).forEach((entry, idx) => {
      const ticker = entry.ticker || '';
      const userCn = entry.cn_name || '';
      // 计算最终显示名：用户 cn_name 优先，否则查内置表
      const builtin = (SYMBOL_CONFIG_BY_MK[draftMk] || {})[ticker];
      const finalCn = userCn || (builtin ? builtin.cn_name : '');
      const builtinShort = builtin ? builtin.short : ticker;
      // 仅当 cn_name 跟 ticker / short 都不一样时才双行显示，避免 'BTC · BTC'
      let displayText;
      if (finalCn && finalCn !== ticker && finalCn !== builtinShort) {
        displayText = `${ticker}  ·  ${finalCn}`;
      } else {
        displayText = ticker;
      }
      const row = document.createElement('div');
      row.className = 'settings-row';
      row.innerHTML = `<div class="row-text">${displayText}</div>`;
      const btn = document.createElement('button');
      btn.className = 'del-btn';
      btn.textContent = '✕';
      btn.onclick = () => { draftSymsByMk[draftMk].splice(idx, 1); renderSettings(); };
      row.appendChild(btn);
      symsList.appendChild(row);
    });

    // 当前 (draftMk, draftRngIv) 的时段列表
    const rngsList = document.getElementById('set-rngs-list');
    rngsList.innerHTML = '';
    const curIvName = draftRngIvByMk[draftMk];
    ((draftRngsByMkIv[draftMk] || {})[curIvName] || []).forEach((r, idx) => {
      const row = document.createElement('div');
      row.className = 'settings-row';
      row.innerHTML = `<div class="row-text">${r.label}<small>${r.start}  →  ${r.end}</small></div>`;
      const btn = document.createElement('button');
      btn.className = 'del-btn';
      btn.textContent = '✕';
      btn.onclick = () => { draftRngsByMkIv[draftMk][curIvName].splice(idx, 1); renderSettings(); };
      row.appendChild(btn);
      rngsList.appendChild(row);
    });

    document.getElementById('status-set').textContent = '';
    document.getElementById('status-set').className = '';
  }

  function addSettingSym() {
    const input = document.getElementById('set-sym-input');
    const cnInput = document.getElementById('set-sym-cn-input');
    const ticker = input.value.trim().toUpperCase();
    const cn = cnInput ? cnInput.value.trim() : '';
    if (!ticker) return;
    const tx = i18n[draftLang];
    // 按 ticker 去重
    const existing = new Set((draftSymsByMk[draftMk] || []).map(s => s.ticker));
    if (existing.has(ticker)) {
      flashSetStatus(tx.sym_dup, 'err'); return;
    }
    draftSymsByMk[draftMk].push({ticker: ticker, cn_name: cn});
    input.value = '';
    if (cnInput) cnInput.value = '';
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
    const curIvName = draftRngIvByMk[draftMk];
    draftRngsByMkIv[draftMk][curIvName].push({label: lbl, start: st, end: en});
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
    // 校验：每个 market 至少 1 个标的，每个 (market, iv) 至少 1 条 range
    for (const mk of MARKETS) {
      if ((draftSymsByMk[mk] || []).length === 0) {
        draftMk = mk;
        renderSettings();
        flashSetStatus(tx.sym_min(tx.mk[mk] || mk), 'err');
        return;
      }
      for (const iv of ENTRY_INTERVALS_BY_MK[mk]) {
        const rs = (draftRngsByMkIv[mk] || {})[iv] || [];
        if (rs.length === 0) {
          draftMk = mk;
          draftRngIvByMk[mk] = iv;
          renderSettings();
          flashSetStatus(tx.rng_min(tx.mk[mk] || mk, tx.iv[iv] || iv), 'err');
          return;
        }
      }
    }

    const payload = {
      language: draftLang,
      market:   draftMk,
    };
    MARKETS.forEach(mk => {
      payload[mk] = {
        symbols: [...draftSymsByMk[mk]],
        ranges:  {},
      };
      ENTRY_INTERVALS_BY_MK[mk].forEach(iv => {
        payload[mk].ranges[iv] = draftRngsByMkIv[mk][iv].map(r => ({...r}));
      });
    });

    try {
      const resp = await fetch('/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      const data = await resp.json();
      if (!data.ok) {
        flashSetStatus(tx.set_save_err + ': ' + data.error, 'err');
        return;
      }
      // 应用到运行时（settings 端 symbols 是 v4 dict 数组，前端拆成两份用）
      const ns = data.settings;
      MARKETS.forEach(mk => {
        const symList = ns[mk].symbols || [];
        // 兼容 v3 字符串（理论上不会再来，但稳健点）和 v4 dict 两种
        SYMBOLS_BY_MK[mk] = symList.map(s =>
          typeof s === 'string' ? s : (s.ticker || ''));
        // 同步刷新用户 cn_name 字典
        const nameMap = {};
        symList.forEach(s => {
          if (typeof s === 'object' && s.ticker) {
            nameMap[s.ticker] = s.cn_name || '';
          }
        });
        USER_CN_NAMES_BY_MK[mk] = nameMap;
        RANGES_BY_MK_IV[mk] = ns[mk].ranges;
      });
      if (MARKETS.includes(ns.market)) selMarket = ns.market;
      // 校正每个 market 的选中状态
      MARKETS.forEach(mk => {
        if (!SYMBOLS_BY_MK[mk].includes(selSymbolByMk[mk])) {
          selSymbolByMk[mk] = SYMBOLS_BY_MK[mk][0] || null;
        }
        ENTRY_INTERVALS_BY_MK[mk].forEach(iv => {
          const rs = (RANGES_BY_MK_IV[mk] || {})[iv] || [];
          const cur = selRngIdxByMkIv[mk][iv];
          if (cur === undefined || cur >= rs.length) {
            selRngIdxByMkIv[mk][iv] = Math.max(0, rs.length - 1);
          }
        });
      });
      switchLang(ns.language, false);
      buildMarketPicker();
      buildSymbolPicker();
      buildIntervalPicker();
      buildRangePicker();
      flashSetStatus(tx.set_saved, 'ok');
      setTimeout(() => showView(prevView), 600);
    } catch (e) {
      flashSetStatus(tx.set_save_err + ': ' + (e.message || e), 'err');
    }
  }

  // ── 语言切换（顶栏快速切换）──────────────────────────────────────
  async function switchLang(lang, persist) {
    curLang = lang;
    const tx = i18n[lang];
    document.getElementById('title').textContent          = tx.title;
    document.getElementById('lbl-market').textContent     = tx.market;
    document.getElementById('lbl-symbol').textContent     = tx.symbol;
    document.getElementById('lbl-interval').textContent   = tx.interval;
    document.getElementById('lbl-range').textContent      = tx.timerange;
    document.getElementById('gen-btn').textContent        = tx.generate;
    document.getElementById('btn-reset-text').textContent = tx.reset;
    document.getElementById('btn-en').classList.toggle('active', lang==='en');
    document.getElementById('btn-zh').classList.toggle('active', lang==='zh');
    // market 按钮文本跟语言变
    buildMarketPicker();
    // 入口周期按钮文本跟语言变
    buildIntervalPicker();
    if (stack.length > 0) {
      buildCrumbs();
      buildSubGrid();
    }
    if (curSyms().length === 0) buildSymbolPicker();
    buildRangePicker();
    if (persist) {
      try {
        const cur = await fetch('/settings').then(r => r.json());
        await fetch('/settings', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({...cur, language: lang}),
        });
      } catch (e) { /* 静默 */ }
    }
  }

  // ── 初始化 ───────────────────────────────────────────────────────
  buildMarketPicker();
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
    # 注入 plot_kline 的 SYMBOL_CONFIG_BY_MARKET 让前端按当前语言显示
    # short 或 cn_name。前端拿不到这个表的话，A 股港股按钮就只能秃秃地
    # 显示 ticker，看不出哪只股。
    from plot_kline import SYMBOL_CONFIG_BY_MARKET
    # 从 v4 settings 拆出两份：纯 ticker 数组（业务用） + ticker→cn_name 字典（显示用）
    symbols_by_mk = {mk: settings.extract_tickers(cur[mk]['symbols'])
                     for mk in MARKETS}
    user_cn_names_by_mk = {mk: settings.extract_name_map(cur[mk]['symbols'])
                           for mk in MARKETS}
    return render_template_string(
        HTML,
        markets_json                = json.dumps(list(MARKETS)),
        entry_intervals_by_mk_json  = json.dumps({mk: list(ENTRY_INTERVALS_BY_MARKET[mk])
                                                  for mk in MARKETS}),
        next_interval_by_mk_json    = json.dumps(NEXT_INTERVAL_BY_MARKET),
        interval_minutes_json       = json.dumps(INTERVAL_MINUTES),
        target_json                 = json.dumps(BARS_PER_SEGMENT_TARGET),
        language_json               = json.dumps(cur['language']),
        market_json                 = json.dumps(cur['market']),
        symbols_by_mk_json          = json.dumps(symbols_by_mk),
        ranges_by_mk_iv_json        = json.dumps({mk: cur[mk]['ranges']
                                                  for mk in MARKETS},
                                                 ensure_ascii=False),
        # 中文名字典：保留 UTF-8 而非 \uXXXX 转义，让 page source 直接可读
        symbol_config_by_mk_json    = json.dumps(SYMBOL_CONFIG_BY_MARKET,
                                                 ensure_ascii=False),
        # 用户在 settings 里给标的设的中文名（v4 新增）。优先级高于内置表。
        user_cn_names_by_mk_json    = json.dumps(user_cn_names_by_mk,
                                                 ensure_ascii=False),
    )


@app.route('/generate', methods=['POST'])
def generate():
    data = request.get_json() or {}
    market   = data.get('market',   'crypto')
    symbol   = data.get('symbol',   'BTCUSDT')
    interval = data.get('interval', 'weekly')
    start    = data.get('start')
    end      = data.get('end')
    if market not in MARKETS:
        return jsonify({'ok': False, 'error': f'invalid market: {market}'})
    try:
        img_b64, actual_start, actual_end, bars = generate_chart(
            market, symbol, interval, start, end
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


# ── 设置端点 ─────────────────────────────────────────────────────────
@app.route('/settings', methods=['GET'])
def get_settings():
    return jsonify(settings.load_settings())


@app.route('/settings', methods=['POST'])
def post_settings():
    """
    校验并保存设置。请求体（v2 双 market）：
        {
          language: 'en'|'zh',
          market:   'crypto'|'stock',
          crypto:   { symbols: [...], ranges: { weekly:[...], 3day:[...], daily:[...] } },
          stock:    { symbols: [...], ranges: { weekly:[...], daily:[...] } }
        }
    任一字段不合法则整体拒绝。
    """
    data = request.get_json() or {}

    language = data.get('language')
    if language not in ('en', 'zh'):
        return jsonify({'ok': False, 'error': 'invalid language'})

    market = data.get('market')
    if market not in MARKETS:
        return jsonify({'ok': False, 'error': f'invalid market: {market!r}'})

    new_settings = {'language': language, 'market': market}

    for mk in MARKETS:
        blk = data.get(mk)
        if not isinstance(blk, dict):
            return jsonify({'ok': False, 'error': f"missing '{mk}' block"})

        syms = blk.get('symbols')
        if not isinstance(syms, list) or not syms:
            return jsonify({'ok': False,
                            'error': f"{mk}.symbols must be a non-empty list"})
        clean_syms = []
        seen_tickers = set()
        for s in syms:
            # 接受 v4 dict {ticker, cn_name} 或向后兼容的 v3 字符串
            if isinstance(s, dict):
                ticker = s.get('ticker', '')
                cn_name = s.get('cn_name', '') or ''
                if not isinstance(ticker, str) or not ticker.strip():
                    return jsonify({'ok': False,
                                    'error': f'invalid symbol entry in {mk}: missing ticker'})
                if not isinstance(cn_name, str):
                    cn_name = ''
                ticker = ticker.strip().upper()
                cn_name = cn_name.strip()
            elif isinstance(s, str) and s.strip():
                ticker = s.strip().upper()
                cn_name = ''
            else:
                return jsonify({'ok': False,
                                'error': f'invalid symbol entry in {mk}'})
            if ticker in seen_tickers:
                continue   # 静默去重
            seen_tickers.add(ticker)
            clean_syms.append({'ticker': ticker, 'cn_name': cn_name})

        rngs = blk.get('ranges')
        if not isinstance(rngs, dict):
            return jsonify({'ok': False,
                            'error': f"{mk}.ranges must be a dict keyed by interval"})

        clean_rngs = {}
        for iv in get_entry_intervals(mk):
            rl = rngs.get(iv)
            if not isinstance(rl, list) or not rl:
                return jsonify({'ok': False,
                                'error': f"{mk}.ranges['{iv}'] must be a non-empty list"})
            clean_list = []
            for r in rl:
                if not isinstance(r, dict):
                    return jsonify({'ok': False,
                                    'error': f"invalid range entry in {mk}.{iv}"})
                label = (r.get('label') or '').strip()
                start = (r.get('start') or '').strip()
                end_  = (r.get('end')   or '').strip()
                if not (label and start and end_):
                    return jsonify({'ok': False,
                                    'error': f"range entry in {mk}.{iv} missing fields"})
                try:
                    s_dt = settings.validate_date_str(start)
                    e_dt = settings.validate_date_str(end_)
                except Exception:
                    return jsonify({'ok': False,
                                    'error': f"invalid date format in {mk}.{iv}/{label}"})
                if e_dt <= s_dt:
                    return jsonify({'ok': False,
                                    'error': f"end must be after start in {mk}.{iv}/{label}"})
                clean_list.append({'label': label, 'start': start, 'end': end_})
            clean_rngs[iv] = clean_list

        new_settings[mk] = {'symbols': clean_syms, 'ranges': clean_rngs}

    try:
        settings.save_settings(new_settings)
    except Exception as e:
        return jsonify({'ok': False, 'error': f'save failed: {e}'})

    return jsonify({'ok': True, 'settings': new_settings})


if __name__ == '__main__':
    print("启动服务: http://0.0.0.0:5000")
    print("手机访问: http://<本机IP>:5000")
    app.run(host='0.0.0.0', port=5000, debug=False)
