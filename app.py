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

from flask import Flask, render_template_string, request, jsonify, session, redirect, url_for

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)

from plot_kline import render_chart, serialize_divergences
from navigation import (NEXT_INTERVAL_BY_MARKET, INTERVAL_MINUTES,
                        TRADING_HOURS_PER_DAY, INTRADAY_INTERVALS)
from config import DIVERGENCE_DRILL_BARS_BEFORE, DIVERGENCE_DRILL_BARS_AFTER
import settings
from settings import MARKETS, ENTRY_INTERVALS_BY_MARKET, get_entry_intervals

app = Flask(__name__)
app.secret_key = os.environ.get("KLINE_SECRET_KEY", "change-me-in-production-$(id -u)")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"



# ── 登录页 HTML ───────────────────────────────────────────────────────────
LOGIN_HTML = r"""
<!DOCTYPE html>
<html lang="zh" id="html-root">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>K线系统 · 登录</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #1e1e2e; color: #e0e0f0;
      font-family: 'Segoe UI', 'Noto Sans', sans-serif;
      min-height: 100vh;
      display: flex; align-items: center; justify-content: center;
      padding: 20px;
    }
    .card {
      background: #2a2a3e; border: 1px solid #44446a;
      border-radius: 16px; padding: 40px 32px;
      width: 100%; max-width: 360px;
    }
    h1 {
      font-size: 1.4rem; color: #7c6af7;
      text-align: center; margin-bottom: 6px;
    }
    .subtitle {
      text-align: center; color: #8888aa;
      font-size: 0.85rem; margin-bottom: 32px;
    }
    label {
      display: block; font-size: 0.82rem; color: #8888aa;
      margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px;
    }
    input[type="text"], input[type="password"] {
      width: 100%; padding: 12px 14px;
      background: #1e1e2e; border: 1px solid #44446a;
      border-radius: 8px; color: #e0e0f0;
      font-size: 1rem; margin-bottom: 18px;
      outline: none; transition: border-color 0.2s;
    }
    input:focus { border-color: #7c6af7; }
    .btn {
      width: 100%; padding: 13px;
      background: #7c6af7; color: white;
      border: none; border-radius: 8px;
      font-size: 1rem; font-weight: bold;
      cursor: pointer; transition: background 0.2s;
    }
    .btn:hover { background: #9b8dff; }
    .error {
      background: #3a1e2e; border: 1px solid #ff5566;
      border-radius: 8px; padding: 10px 14px;
      color: #ff5566; font-size: 0.88rem;
      margin-bottom: 18px; text-align: center;
    }
    .no-users {
      background: #1e2e3a; border: 1px solid #4466aa;
      border-radius: 8px; padding: 14px;
      color: #88aacc; font-size: 0.85rem;
      margin-bottom: 18px; line-height: 1.6;
    }
    .no-users code {
      background: #2a3a4e; border-radius: 4px;
      padding: 1px 6px; font-size: 0.82rem;
      color: #aaccff;
    }
  </style>
</head>
<body>
<div class="card">
  <h1>📈 K线系统</h1>
  <p class="subtitle">请登录以继续</p>

  {% if no_users %}
  <div class="no-users">
    尚未创建任何用户。请先在服务器上运行：<br>
    <code>python3 manage_users.py add 用户名 密码</code>
  </div>
  {% endif %}

  {% if error %}
  <div class="error">{{ error }}</div>
  {% endif %}

  <form method="post" action="/login">
    <label>用户名</label>
    <input type="text" name="username" autocomplete="username"
           placeholder="请输入用户名" value="{{ username_val }}" required>
    <label>密码</label>
    <input type="password" name="password" autocomplete="current-password"
           placeholder="请输入密码" required>
    <button class="btn" type="submit">登 录</button>
  </form>
</div>
</body>
</html>
"""


# ── 认证路由 ──────────────────────────────────────────────────────────────
@app.before_request
def require_login():
    """所有路由（除 /login /logout）都需要登录。"""
    if request.endpoint in ('login', 'logout', 'static'):
        return
    if 'username' not in session:
        return redirect(url_for('login', next=request.path))


@app.route('/login', methods=['GET', 'POST'])
def login():
    from settings import load_users, verify_password
    users = load_users()
    no_users = len(users) == 0

    if request.method == 'GET':
        return render_template_string(
            LOGIN_HTML,
            error='', username_val='', no_users=no_users,
        )

    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')

    if not verify_password(username, password):
        return render_template_string(
            LOGIN_HTML,
            error='用户名或密码错误',
            username_val=username,
            no_users=no_users,
        )

    session['username'] = username
    next_url = request.args.get('next', '/')
    # 安全校验：只允许跳转到站内路径
    if not next_url.startswith('/'):
        next_url = '/'
    return redirect(next_url)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))



def generate_chart(market, symbol, interval, start_str, end_str,
                   locked_anchor=None):
    """渲染并返回 (b64, actual_start_iso, actual_end_iso, bars_count, divs)

    divs 是 serialize_divergences 输出的简化 dict 列表,前端用它渲染
    "背离钻取卡片"。已按 s3_end 升序(时间序)。

    locked_anchor : dict | None
        锁定锚点信息包,提供则主面板 MA99 在锚点 s3 时间窗内染红。
    """
    fig, df, divs = render_chart(market, symbol, interval,
                                 start_str or None, end_str or None,
                                 locked_anchor=locked_anchor or None)
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight', pad_inches=0.8)
    plt.close(fig)
    buf.seek(0)
    img_b64 = base64.b64encode(buf.read()).decode('utf-8')
    return (img_b64,
            df.index[0].isoformat(),
            df.index[-1].isoformat(),
            int(len(df)),
            serialize_divergences(df, divs))


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

    .user-badge { display: flex; align-items: center; gap: 6px;
                  background: #2a2a3e; border: 1px solid #44446a;
                  border-radius: 6px; padding: 4px 10px;
                  font-size: 0.82rem; color: #8888aa; }
    .user-badge span { color: #e0e0f0; }
    .logout-btn { background: #2a2a3e; border: 1px solid #44446a;
                  border-radius: 6px; padding: 4px 12px;
                  font-size: 0.82rem; cursor: pointer; user-select: none;
                  transition: all 0.15s; color: #8888aa; text-decoration: none;
                  display: inline-flex; align-items: center; }
    .logout-btn:hover { border-color: #ff5566; color: #ff5566; }

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
    .grid.divs       { grid-template-columns: 1fr; gap: 6px; }
    .grid.divs.cols2 { grid-template-columns: repeat(2, 1fr); }
    .grid.divs.cols3 { grid-template-columns: repeat(3, 1fr); }

    .sub-card { background: #2a2a3e; border: 1px solid #44446a;
                border-radius: 8px; padding: 10px; text-align: left;
                cursor: pointer; transition: all 0.15s; user-select: none; }
    .sub-card:hover { border-color: #f7a26a; }
    .sub-card-head { font-size: 0.75rem; color: #8888aa; margin-bottom: 2px; }
    .sub-card-body { font-size: 0.92rem; color: #e0e0f0; }

    /* 背离钻取卡片:跟 sub-card 同款圆角,但卡片头颜色按 kind 区分 */
    .div-card { background: #2a2a3e; border: 1px solid #44446a;
                border-radius: 8px; padding: 10px; text-align: left;
                cursor: pointer; transition: all 0.15s; user-select: none; }
    .div-card:hover { border-color: #f7a26a; }
    .div-card.locked { border-color: #f7a26a; border-width: 1.5px; }
    .div-card.locked:hover { border-color: #ffb070; }
    /* provisional(未完成)信号不可钻取:去掉指针光标和 hover 高亮 */
    .div-card.provisional { cursor: default; opacity: 0.8; }
    .div-card.provisional:hover { border-color: #44446a; }
    .div-card-head { font-size: 0.78rem; margin-bottom: 2px; font-weight: 500; }
    .div-card-head.bullish     { color: #ff5566; }
    .div-card-head.bearish     { color: #22cc44; }
    .div-card-head.provisional { color: #1e90ff; }
    .div-card-head.locked      { color: #f7a26a; }
    .div-card-body { font-size: 0.92rem; color: #e0e0f0; }
    /* provisional 卡片底部的灰字说明 */
    .div-card-note { font-size: 0.72rem; color: #6c7a99;
                     margin-top: 4px; font-style: italic; }
    .div-empty { color: #8888aa; font-size: 0.85rem; padding: 4px 0; }

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
      <div class="user-badge">👤 <span>{{ username }}</span></div>
      <a class="logout-btn" href="/logout">退出</a>
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
  const DIV_DRILL_BARS_BEFORE      = {{ div_drill_bars_before_json | safe }};
  const DIV_DRILL_BARS_AFTER       = {{ div_drill_bars_after_json | safe }};
  // 交易日因子配置（跟后端 navigation 一致）
  const TRADING_HOURS_PER_DAY      = {{ trading_hours_per_day_json | safe }};
  const INTRADAY_INTERVALS         = {{ intraday_intervals_json | safe }};
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
      reset:      '↺ Start over',
      generating: 'Generating...',
      done:       bars => `Done ✓ (${bars} bars)`,
      error:      'Error ✗',
      end_msg:    '(End of pyramid — no further drill-down)',
      div_drill:        'Click a divergence to drill into next level:',
      div_locked_head:  '🔒 Locked anchor — continue drilling',
      div_none:         '(No divergence detected in current view)',
      div_bullish:      'Bullish',
      div_bearish:      'Bearish',
      div_extreme:      'Extreme',
      div_provisional:  'Provisional — not yet closed, drill-down disabled',
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
      reset:      '↺ 重新开始',
      generating: '正在生成...',
      done:       bars => `完成 ✓ (${bars} 根)`,
      error:      '错误 ✗',
      end_msg:    '(已到金字塔末端 — 下方无更细周期)',
      div_drill:        '点击下方背离卡片,钻取到次级别:',
      div_locked_head:  '🔒 锁定锚点钻取链 — 继续钻取下一级',
      div_none:         '(当前视图未检测到背离信号)',
      div_bullish:      '底背离',
      div_bearish:      '顶背离',
      div_extreme:      '极值',
      div_provisional:  '未完成 · 尚未封口,暂不可钻取',
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

  // ── 周期映射 + 背离钻取窗口 ─────────────────────────────────────
  function nextIntervalOf(market, iv) {
    const m = NEXT_INTERVAL_BY_MK[market] || {};
    return m[iv] || null;
  }
  // 钻取窗口:peak ± (BEFORE, AFTER) × factor 根次级 K 线时长。
  // factor 跟后端 navigation.compute_lookback_factor 一致:
  //   - crypto / stock daily 及以上 = 1.0
  //   - stock intraday (1h 等) = (24/交易小时数)×(7/5),A 股 1h 约 8.4
  function computeLookbackFactor(market, interval) {
    if (market === 'crypto') return 1.0;
    if (INTRADAY_INTERVALS.indexOf(interval) < 0) return 1.0;
    const hoursPerDay = TRADING_HOURS_PER_DAY[market] !== undefined
        ? TRADING_HOURS_PER_DAY[market] : 6.5;
    return (24.0 / hoursPerDay) * (7.0 / 5.0);
  }
  function computeDivDrillWindow(peakIso, nextIv, market) {
    const peakMs = new Date(peakIso).getTime();
    const min = INTERVAL_MINUTES[nextIv];
    const factor = computeLookbackFactor(market, nextIv);
    const before = DIV_DRILL_BARS_BEFORE * min * factor * 60 * 1000;
    const after  = DIV_DRILL_BARS_AFTER  * min * factor * 60 * 1000;
    return [
      new Date(peakMs - before).toISOString(),
      new Date(peakMs + after).toISOString(),
    ];
  }
  // 从 div(serializeDivergences 输出)抽出锚点信息包
  // parent_interval 是产出该 div 的图所在周期,带在 anchor 里一路传给后端,
  // MA99 染色时用它把"父级 K 线 open_time"扩成"父级 K 线覆盖的物理时间窗"。
  function divToAnchor(d, parentInterval) {
    return {
      peak_iso:     d.peak_iso,
      s3_start_iso: d.s3_start_iso || null,
      s3_end_iso:   d.s3_end_iso   || null,
      kind:         d.kind || 'bullish',
      parent_interval: parentInterval,
    };
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
  // pushAndRender 第 6 参数 lockedAnchor (object|null|undefined):
  //   { peak_iso, s3_start_iso, s3_end_iso, kind }
  // 提供时表示当前栈帧属于一条"锁定锚点钻取链"——锚点信息整条链上不变,
  // 子图主面板 MA99 在锚点 s3 时间窗内染红。
  async function pushAndRender(market, symbol, interval, startIso, endIso,
                               lockedAnchor) {
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
          locked_anchor: lockedAnchor || null,
        })
      });
      const data = await resp.json();
      if (!data.ok) throw new Error(data.error);

      stack.push({
        market, symbol, interval,
        start: data.actual_start,
        end:   data.actual_end,
        bars:  data.bars,
        divs:  data.divs || [],
        lockedAnchor: lockedAnchor || null,
      });

      img.src = 'data:image/png;base64,' + data.img;
      img.style.display = 'block';
      status.textContent = tx.done(data.bars);
      status.className = 'ok';

      buildCrumbs();
      buildDivGrid();
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

  // 按父级周期颗粒度格式化 peak 时间
  function fmtPeakLabel(peakIso, parentInterval) {
    if (!peakIso) return '';
    const d = new Date(peakIso);
    const pad = n => String(n).padStart(2, '0');
    if (['15m','30m','1h','4h'].includes(parentInterval)) {
      return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
  }

  // 背离钻取区:两种模式(非锁定列出所有背离 / 锁定单按钮继续)
  function buildDivGrid() {
    const top = stack[stack.length - 1];
    const nextIv = nextIntervalOf(top.market, top.interval);
    const section = document.getElementById('sub-section');
    const grid    = document.getElementById('sub-grid');
    const tx      = i18n[curLang];

    if (!nextIv) {
      section.style.display = 'block';
      document.getElementById('sub-hint').textContent = tx.end_msg;
      grid.innerHTML = '';
      return;
    }

    section.style.display = 'block';
    document.getElementById('sub-hint').textContent = tx.div_drill;

    // ── 锁定模式:单按钮 ─────────────────────────────────────────
    if (top.lockedAnchor) {
      grid.className = 'grid divs';
      const peakStr = fmtPeakLabel(top.lockedAnchor.peak_iso, top.interval);
      const el = document.createElement('div');
      el.className = 'div-card locked';
      el.innerHTML =
        `<div class="div-card-head locked">${tx.div_locked_head}</div>` +
        `<div class="div-card-body">▸ ${ivLabel(nextIv)}    @ ${peakStr}</div>`;
      el.onclick = () => drillWithAnchor(top.market, top.symbol, nextIv,
                                          top.lockedAnchor);
      grid.innerHTML = '';
      grid.appendChild(el);
      return;
    }

    // ── 非锁定模式:列出本图所有背离 ──────────────────────────────
    const divs = top.divs || [];
    if (divs.length === 0) {
      grid.className = 'grid divs';
      grid.innerHTML = `<div class="div-empty">${tx.div_none}</div>`;
      return;
    }

    const n = divs.length;
    grid.className = 'grid divs' +
      (n >= 5 ? ' cols3' : (n >= 3 ? ' cols2' : ''));

    grid.innerHTML = '';
    divs.forEach((d, idx) => {
      const isProv = !!d.provisional;
      const kindText = d.kind === 'bullish' ? tx.div_bullish : tx.div_bearish;
      const cssKind = isProv ? 'provisional' :
                      (d.kind === 'bullish' ? 'bullish' : 'bearish');
      const isExtreme = d.level === 0;
      const provSuffix = isProv ? ' ?' : '';
      const ratioPct = Math.round(d.ratio * 100);
      // level 0 = 价格极值标识（标准三段背离漏掉的那类）：无 Lv / 无面积比
      const metaText = isExtreme ? tx.div_extreme
                                 : `L${d.level}  ${ratioPct}%${provSuffix}`;
      const peakStr = fmtPeakLabel(d.peak_iso, top.interval);

      const el = document.createElement('div');
      el.className = 'div-card ' + cssKind;
      el.innerHTML =
        `<div class="div-card-head ${cssKind}">` +
          `${idx+1}/${n} · ${kindText} · ${metaText}` +
        `</div>` +
        `<div class="div-card-body">@ ${peakStr}  ▸ ${ivLabel(nextIv)}</div>` +
        (isProv ? `<div class="div-card-note">${tx.div_provisional}</div>` : '');
      // 点击 = 用 div 的完整锚点(peak_iso + s3 时间窗 + kind + parent_interval)启动锁定链。
      // provisional(未完成)信号还未封口,不允许钻取——与公众号宣传一致,不绑定点击。
      if (!isProv) {
        el.onclick = () => drillWithAnchor(top.market, top.symbol, nextIv,
                                            divToAnchor(d, top.interval));
      }
      grid.appendChild(el);
    });
  }

  // 通用钻取入口:启动锁定链 / 沿锁定链继续都走这里
  function drillWithAnchor(market, symbol, nextIv, anchor) {
    const [s, e] = computeDivDrillWindow(anchor.peak_iso, nextIv, market);
    pushAndRender(market, symbol, nextIv, s, e, anchor);
  }

  function popTo(idx) {
    const target = stack[idx];
    stack = stack.slice(0, idx);
    // 回到 idx 这一帧 = 恢复它的锁定状态。如果 target 自己就在锁定链中,
    // 恢复后 UI 仍是锁定模式;否则回到"列出所有背离"模式。
    pushAndRender(target.market, target.symbol, target.interval,
                  target.start, target.end,
                  target.lockedAnchor || undefined);
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
      buildDivGrid();
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
    cur = settings.load_settings_for_user(session["username"])
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
        div_drill_bars_before_json  = json.dumps(DIVERGENCE_DRILL_BARS_BEFORE),
        div_drill_bars_after_json   = json.dumps(DIVERGENCE_DRILL_BARS_AFTER),
        # 交易日因子用：让前端 JS 也能算出 (24/交易小时数)×(7/5)，跟后端 navigation.compute_lookback_factor 保持一致
        trading_hours_per_day_json  = json.dumps(TRADING_HOURS_PER_DAY),
        intraday_intervals_json     = json.dumps(list(INTRADAY_INTERVALS)),
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
        username                    = session['username'],
    )


@app.route('/generate', methods=['POST'])
def generate():
    data = request.get_json() or {}
    market   = data.get('market',   'crypto')
    symbol   = data.get('symbol',   'BTCUSDT')
    interval = data.get('interval', 'weekly')
    start    = data.get('start')
    end      = data.get('end')
    # 锚点信息包(可选)。前端 pushAndRender 传 lockedAnchor → 服务端这里收
    raw_anchor = data.get('locked_anchor')
    locked_anchor = raw_anchor if isinstance(raw_anchor, dict) else None

    if market not in MARKETS:
        return jsonify({'ok': False, 'error': f'invalid market: {market}'})
    try:
        img_b64, actual_start, actual_end, bars, divs = generate_chart(
            market, symbol, interval, start, end,
            locked_anchor=locked_anchor,
        )
        return jsonify({
            'ok': True,
            'img': img_b64,
            'actual_start': actual_start,
            'actual_end':   actual_end,
            'bars':         bars,
            'divs':         divs,
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


# ── 设置端点 ─────────────────────────────────────────────────────────
@app.route('/settings', methods=['GET'])
def get_settings():
    return jsonify(settings.load_settings_for_user(session["username"]))


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
        settings.save_settings_for_user(session["username"], new_settings)
    except Exception as e:
        return jsonify({'ok': False, 'error': f'save failed: {e}'})

    return jsonify({'ok': True, 'settings': new_settings})


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='K线系统 Web 服务（多用户版）')
    parser.add_argument('--port', type=int, default=5000, help='监听端口（默认 5000）')
    parser.add_argument('--host', default='0.0.0.0', help='监听地址（默认 0.0.0.0）')
    args = parser.parse_args()
    print(f"启动服务: http://{args.host}:{args.port}")
    print(f"手机访问: http://<本机IP>:{args.port}")
    print("提示: 首次使用请先运行 python3 manage_users.py add <用户名> <密码>")
    import os as _os
    if _os.environ.get('KLINE_SECRET_KEY', '').startswith('change-me'):
        print("警告: 建议设置环境变量 KLINE_SECRET_KEY=<随机字符串> 以加强 session 安全")
    app.run(host=args.host, port=args.port, debug=False)
