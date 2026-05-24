"""
panorama.py — 全景图（多周期 K 线垂直堆叠）
==============================================

入口
----
仅在【周线】图的背离卡片旁出现"🌐 全景图"按钮。
点击后跳到新页面 /panorama，按"自上而下逐级钻取"的逻辑，
一次性生成从周线到该 market 最低级别的所有 K 线图，
原始尺寸保留，外层容器允许横向滚动。

逻辑约束
--------
1. 完全复用现有 render_chart() 渲染单张图；不改任何参数。
2. 子级窗口 = compute_divergence_drill_window(上一级 peak_ts, 当前周期, market)
   与用户手工钻取得到的窗口一模一样。
3. 每一级会从结果中找出与本级 lockedAnchor 时间最近的同向背离，
   取其 peak_iso 作为下一级的锚点。这模拟"用户在锁定链中继续点钻取下一级"。
4. 周线本身的图由调用方提供（s1_start_iso / s3_end_iso / kind / peak_iso），
   不再二次拉取。

注册
----
app.py 末尾调用：
    from panorama import register_panorama_routes
    register_panorama_routes(app)
"""

import os
import sys
import datetime as _dt

import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)

from plot_kline import render_chart
from navigation import (
    NEXT_INTERVAL_BY_MARKET,
    compute_divergence_drill_window,
    to_binance_str,
)


# ── 常量 ──────────────────────────────────────────────────────────────────
IV_LABEL = {
    'weekly': '周线', '3day': '3日线', 'daily': '日线',
    '4h': '4小时线', '1h': '1小时线', '30m': '30分钟线', '15m': '15分钟线',
}
MK_LABEL = {
    'crypto': '虚拟币', 'us_stock': '美股',
    'cn_stock': 'A股', 'hk_stock': '港股',
}


# ═══════════════════════════════════════════════════════════════════════════
# 内部工具
# ═══════════════════════════════════════════════════════════════════════════

def _to_ts(iso: str) -> pd.Timestamp:
    """ISO 字符串 → tz-naive Timestamp（用于比较）"""
    ts = pd.Timestamp(iso)
    return ts.tz_localize(None) if ts.tzinfo is not None else ts


def _fmt_range_for_title(start_iso: str, end_iso: str, interval: str) -> str:
    """生成图卡标题的时间范围（与现有 K 线图标题保持一致风格）"""
    s = _to_ts(start_iso)
    e = _to_ts(end_iso)
    fmt = '%Y-%m-%d %H:%M' if interval in ('15m', '30m', '1h', '4h') else '%Y-%m-%d'
    return f"{s.strftime(fmt)} ~ {e.strftime(fmt)}"


def _tf_chain(market: str, top_interval: str) -> list[str]:
    """从 top_interval 开始，沿 NEXT_INTERVAL_BY_MARKET 向下到最低级别（含两端）。"""
    chain = [top_interval]
    nxt_map = NEXT_INTERVAL_BY_MARKET.get(market, {})
    cur = top_interval
    while True:
        nxt = nxt_map.get(cur)
        if not nxt:
            break
        chain.append(nxt)
        cur = nxt
    return chain


# ═══════════════════════════════════════════════════════════════════════════
# 核心：单张图渲染（无状态，按需调用）
# ═══════════════════════════════════════════════════════════════════════════

def _render_one_tile(market: str, symbol: str, interval: str,
                     anchor: dict, idx_in_chain: int,
                     top_start_iso: str | None = None,
                     top_end_iso: str | None = None) -> dict:
    """
    渲染单张周期图。完全无状态：所有上下文都从入参导出。

    Parameters
    ----------
    interval     : 当前要渲染的周期
    anchor       : 顶层 anchor（钻取链共享，整条链都用它计算窗口）
    idx_in_chain : 在周期链中的位置（0 = 顶层）；决定是否传 locked_anchor
    top_start_iso, top_end_iso : 仅 idx_in_chain == 0 时使用，
        用来精确复刻"用户主界面看到的顶层图"。
    """
    # 决定时间窗
    if idx_in_chain == 0:
        if top_start_iso and top_end_iso:
            try:
                start_str = to_binance_str(_to_ts(top_start_iso))
                end_str   = to_binance_str(_to_ts(top_end_iso))
            except Exception:
                start_str, end_str = None, None
        else:
            start_str, end_str = None, None
    else:
        try:
            win_s, win_e = compute_divergence_drill_window(
                _to_ts(anchor['peak_iso']),
                interval, market,
                s3_start_iso=anchor.get('s3_start_iso'),
                s3_end_iso=anchor.get('s3_end_iso'),
                parent_iv=anchor.get('parent_interval'),
            )
            start_str = to_binance_str(win_s)
            end_str   = to_binance_str(win_e)
        except Exception as e:
            return {
                'interval':     interval,
                'iv_label':     IV_LABEL.get(interval, interval),
                'title_range':  '',
                'img_b64':      None,
                'bars':         0,
                'actual_start': None,
                'actual_end':   None,
                'error':        f'计算钻取窗口失败: {e}',
            }

    # 渲染
    try:
        fig, df, _divs = render_chart(
            market, symbol, interval,
            start_str, end_str,
            locked_anchor=anchor if idx_in_chain > 0 else None,
        )
        import io, base64
        import matplotlib.pyplot as plt
        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight', pad_inches=0.8)
        plt.close(fig)
        buf.seek(0)
        img_b64 = base64.b64encode(buf.read()).decode('utf-8')

        actual_start = df.index[0].isoformat()
        actual_end   = df.index[-1].isoformat()
        return {
            'interval':     interval,
            'iv_label':     IV_LABEL.get(interval, interval),
            'title_range':  _fmt_range_for_title(actual_start, actual_end, interval),
            'img_b64':      img_b64,
            'bars':         len(df),
            'actual_start': actual_start,
            'actual_end':   actual_end,
            'error':        None,
        }
    except Exception as e:
        return {
            'interval':     interval,
            'iv_label':     IV_LABEL.get(interval, interval),
            'title_range':  '',
            'img_b64':      None,
            'bars':         0,
            'actual_start': None,
            'actual_end':   None,
            'error':        f'渲染失败: {e}',
        }


# ═══════════════════════════════════════════════════════════════════════════
# 首屏构建：仅渲染前 N 张图，剩余周期返回占位
# ═══════════════════════════════════════════════════════════════════════════

def build_eager(market: str, symbol: str, top_anchor: dict,
                top_interval: str = 'weekly',
                top_start_iso: str | None = None,
                top_end_iso: str | None = None,
                eager_count: int = 2) -> list[dict]:
    """
    首屏渲染：周期链前 eager_count 张图立即生成，
    其余周期返回 {'pending': True} 占位条目（前端用 IntersectionObserver
    按需调用 /panorama/tile）。

    Parameters
    ----------
    top_interval : 顶层周期。常用值：
        - 'weekly' → 周线全景 (crypto 7 张 / stock 3 张)
        - '3day'   → 3日线全景 (crypto 6 张)
        - 'daily'  → 日线全景 (crypto 5 张 / stock 2 张)
    top_anchor   : 该顶层周期某个背离的完整 anchor 信息（含 peak_iso 等）。
                   整条钻取链共享此 anchor —— 子级窗口都以 top peak 为中心。
    top_start_iso, top_end_iso : 顶层周期当前的实际时间窗（来自主界面 stack[0]），
        让 panorama 顶层图与用户主界面所见完全一致。
    """
    chain = _tf_chain(market, top_interval)

    anchor = top_anchor.copy()
    anchor.setdefault('kind', 'bullish')

    results: list[dict] = []
    for idx, iv in enumerate(chain):
        if idx < eager_count:
            results.append(_render_one_tile(
                market, symbol, iv, anchor, idx,
                top_start_iso, top_end_iso,
            ))
        else:
            # 占位：前端按需触发渲染
            results.append({
                'interval':     iv,
                'iv_label':     IV_LABEL.get(iv, iv),
                'pending':      True,
                'title_range':  '',
                'img_b64':      None,
                'bars':         0,
                'actual_start': None,
                'actual_end':   None,
                'error':        None,
            })

    return results


# ═══════════════════════════════════════════════════════════════════════════
# 单张图按需端点的入口
# ═══════════════════════════════════════════════════════════════════════════

def render_tile(market: str, symbol: str, interval: str,
                top_anchor: dict,
                top_interval: str = 'weekly',
                top_start_iso: str | None = None,
                top_end_iso: str | None = None) -> dict:
    """
    按需渲染单张图（无状态，供 /panorama/tile JSON 端点调用）。

    interval 必须在该 market 从 top_interval 开始的周期链内；
    不在则返回 error 条目。
    """
    chain = _tf_chain(market, top_interval)
    if interval not in chain:
        return {
            'interval':     interval,
            'iv_label':     IV_LABEL.get(interval, interval),
            'title_range':  '',
            'img_b64':      None,
            'bars':         0,
            'actual_start': None,
            'actual_end':   None,
            'error':        f'interval {interval!r} 不在 {market!r} 从 {top_interval!r} 起的周期链中',
        }
    idx = chain.index(interval)

    anchor = top_anchor.copy()
    anchor.setdefault('kind', 'bullish')

    return _render_one_tile(
        market, symbol, interval, anchor, idx,
        top_start_iso, top_end_iso,
    )


# ═══════════════════════════════════════════════════════════════════════════
# HTML 模板
# ═══════════════════════════════════════════════════════════════════════════

PANORAMA_HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>全景图 · {{ symbol_short }}</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{background:#1e1e2e;color:#e0e0f0;
     font-family:'Segoe UI','Noto Sans SC',sans-serif;
     font-size:14px;line-height:1.5;padding:12px 10px 32px}

/* ── 顶部信息卡 ── */
.hdr{background:#2a2a3e;border:1px solid #44446a;border-radius:10px;
     padding:12px 14px;margin-bottom:10px}
.hdr-title{font-size:14px;font-weight:700;color:#7c6af7;margin-bottom:3px}
.hdr-sub{font-size:11px;color:#9999bb;line-height:1.6}
.hdr-sub strong{color:#e0e0f0}
.hdr-kind-bull{color:#ff5566;font-weight:700}
.hdr-kind-bear{color:#22cc44;font-weight:700}

.crumbs{display:flex;flex-wrap:wrap;gap:4px;font-size:10px;margin-top:8px;align-items:center}
.crumb{background:#3a3a5e;color:#f7a26a;padding:2px 8px;border-radius:4px;
       border:0.5px solid #5a5a7a;line-height:1.4}
.crumb-sep{color:#666688;font-size:9px}

/* ── 图卡 ── */
.tf-card{background:#252538;border:1px solid #3a3a5a;border-radius:10px;
         margin-bottom:8px;overflow:hidden}
.tf-card.source{border-color:#f7a26a;background:#2e2840}
.tf-hdr{display:flex;justify-content:space-between;align-items:center;
        padding:8px 12px;background:#2a2a40;border-bottom:1px solid #3a3a5a;
        flex-wrap:wrap;gap:6px}
.tf-card.source .tf-hdr{background:#3a3024;border-bottom-color:#f7a26a}
.tf-name{font-size:12px;font-weight:700;color:#e0e0f0;display:flex;align-items:center;gap:6px}
.tf-card.source .tf-name{color:#f7a26a}
.src-tag{background:#f7a26a;color:#1e1e2e;font-size:9px;padding:1px 6px;
         border-radius:3px;font-weight:700;letter-spacing:0.05em}
.tf-meta{font-size:10px;color:#8888aa}

/* 图片容器：按手机宽度等比缩放（每张约 380px 宽，约 290px 高）
   原图 ≈ 1300×1000，width:100% 后浏览器自动等比缩放，保持 4:3 视觉比例。
   设计意图：7 张图同屏紧凑展示，方便由高到低观察结构演变。 */
.img-fit{background:#fff;padding:0;line-height:0}
.img-fit img{display:block;width:100%;height:auto}

/* 占位卡：未触发渲染或正在渲染中。撑出约 280px 高，与真实图片差 ±10px。
   data-state 控制内部显示：
     'idle'    → 等待进入视口
     'loading' → spinner + 文字
     'error'   → 错误信息 + 重试按钮 */
.tile-placeholder{
  height:280px;background:#1a1a28;
  display:flex;align-items:center;justify-content:center;
  flex-direction:column;gap:10px;
  color:#6c7a99;font-size:12px;
}
.tile-placeholder[data-state="loading"] .spinner{display:block}
.tile-placeholder[data-state="loading"] .err-box{display:none}
.tile-placeholder[data-state="loading"] .idle-hint{display:none}

.tile-placeholder[data-state="idle"] .spinner{display:none}
.tile-placeholder[data-state="idle"] .err-box{display:none}
.tile-placeholder[data-state="idle"] .idle-hint{display:block}

.tile-placeholder[data-state="error"] .spinner{display:none}
.tile-placeholder[data-state="error"] .idle-hint{display:none}
.tile-placeholder[data-state="error"] .err-box{display:flex}

.spinner{
  width:24px;height:24px;border:2.5px solid #3a3a5a;
  border-top-color:#f7a26a;border-radius:50%;
  animation:tile-spin 0.8s linear infinite;
}
@keyframes tile-spin{to{transform:rotate(360deg)}}
.spinner-text{color:#8888aa;font-size:11px}

.idle-hint{color:#555577;font-size:11px;font-style:italic}

.err-box{
  flex-direction:column;align-items:center;gap:8px;
  padding:16px;text-align:center;
}
.err-msg{color:#ff8080;font-size:12px;max-width:80%;line-height:1.5}
.retry-btn{
  background:#3a3a5e;color:#f7a26a;border:1px solid #f7a26a;
  border-radius:6px;padding:6px 14px;font-size:12px;
  cursor:pointer;user-select:none;
}
.retry-btn:hover{background:#f7a26a;color:#1e1e2e}

.tf-err{padding:14px;color:#ff8080;font-size:12px;background:#2a2a40}

/* 底部 */
.footer{display:flex;gap:8px;margin-top:14px}
.foot-btn{flex:1;text-align:center;background:#2a2a3e;color:#e0e0f0;
          border:1px solid #44446a;border-radius:8px;padding:11px;
          text-decoration:none;font-weight:600;font-size:13px;cursor:pointer}
.foot-btn:hover{border-color:#7c6af7;color:#7c6af7}
.foot-btn.primary{background:#3a3a5e;color:#f7a26a;border-color:#f7a26a}
.gen-time{text-align:center;font-size:10px;color:#555577;margin-top:10px}
</style>
</head>
<body>

<div class="hdr">
  <div class="hdr-title">🌐 {{ symbol_short }} 全景图</div>
  <div class="hdr-sub">
    {{ mk_label }} · 信号源：<strong>{{ top_iv_label }}</strong>
    <span class="{{ 'hdr-kind-bull' if anchor_kind=='bullish' else 'hdr-kind-bear' }}">
      {{ '▲ 底背离' if anchor_kind=='bullish' else '▼ 顶背离' }}
    </span>
    · 极值时间 <strong>{{ anchor_peak_date }}</strong>
  </div>
  <div class="crumbs">
    {% for iv in chain %}
      {% if not loop.first %}<span class="crumb-sep">▸</span>{% endif %}
      <span class="crumb">{{ IV_LABEL[iv] }}</span>
    {% endfor %}
  </div>
</div>

{% for tf in tf_results %}
<div class="tf-card {{ 'source' if loop.first else '' }}"
     data-interval="{{ tf.interval }}"
     {% if tf.pending %}data-pending="1"{% endif %}>
  <div class="tf-hdr">
    <span class="tf-name">
      {{ tf.iv_label }} K-Line
      {% if loop.first %}<span class="src-tag">信号源</span>{% endif %}
    </span>
    <span class="tf-meta">
      {% if tf.error %}
        <span style="color:#ff8080">⚠ {{ tf.error }}</span>
      {% elif tf.pending %}
        <span style="color:#6c7a99">待加载</span>
      {% else %}
        {{ tf.title_range }}  ·  {{ tf.bars }} 根
      {% endif %}
    </span>
  </div>

  {% if tf.error %}
    {# 首屏渲染就失败的（错误信息已显示在标题栏） #}
    <div class="tf-err">{{ tf.error }}</div>
  {% elif tf.pending %}
    {# 占位卡：等待 IntersectionObserver 触发按需渲染 #}
    <div class="tile-placeholder" data-state="idle">
      <div class="idle-hint">⌛ 即将加载…</div>
      <div class="spinner"></div>
      <div class="spinner-text">正在生成 {{ tf.iv_label }}…</div>
      <div class="err-box">
        <div class="err-msg"></div>
        <button class="retry-btn">重试</button>
      </div>
    </div>
  {% else %}
    {# 已渲染图片 #}
    <div class="img-fit">
      <img src="data:image/png;base64,{{ tf.img_b64 }}" alt="{{ tf.iv_label }} K-Line">
    </div>
  {% endif %}
</div>
{% endfor %}

<div class="footer">
  <button class="foot-btn" onclick="window.close()">✕ 关闭</button>
  <button class="foot-btn primary" onclick="window.scrollTo({top:0,behavior:'smooth'})">↑ 回到顶部</button>
</div>
<div class="gen-time">生成时间：{{ generated_at }}</div>

<script>
/* ──────────────────────────────────────────────────────────────────────
   懒加载逻辑
   ──────────────────────────────────────────────────────────────────────
   1. IntersectionObserver 监听 data-pending="1" 的 tf-card
   2. 进入"视口底部 + 200px"时入队
   3. 全局并发上限 2，超出者排队
   4. 单张图请求 /panorama/tile，成功则替换占位为 <img>，失败则显示错误+重试
*/
(function(){
  const TILE_URL = '/panorama/tile';
  const MAX_CONCURRENT = 2;

  // 从 hdr 信息卡里复用全景参数
  const CTX = {
    market:       {{ ctx_market | tojson }},
    symbol:       {{ ctx_symbol | tojson }},
    peak_iso:     {{ ctx_peak_iso | tojson }},
    s3_start_iso: {{ ctx_s3_start_iso | tojson }},
    s3_end_iso:   {{ ctx_s3_end_iso | tojson }},
    kind:         {{ ctx_kind | tojson }},
    top_interval: {{ ctx_top_interval | tojson }},
    top_start:    {{ ctx_top_start | tojson }},
    top_end:      {{ ctx_top_end | tojson }},
  };

  let active = 0;
  const queue = [];

  function pump(){
    while (active < MAX_CONCURRENT && queue.length){
      const task = queue.shift();
      active++;
      task().finally(() => { active--; pump(); });
    }
  }

  function fetchTile(card){
    const interval = card.dataset.interval;
    const placeholder = card.querySelector('.tile-placeholder');
    if (!placeholder) return;
    placeholder.dataset.state = 'loading';

    const params = new URLSearchParams({
      market:       CTX.market,
      symbol:       CTX.symbol,
      interval:     interval,
      peak_iso:     CTX.peak_iso,
      s3_start_iso: CTX.s3_start_iso,
      s3_end_iso:   CTX.s3_end_iso,
      kind:         CTX.kind,
      top_interval: CTX.top_interval,
      top_start:    CTX.top_start,
      top_end:      CTX.top_end,
    });

    return fetch(TILE_URL + '?' + params.toString())
      .then(r => {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(data => {
        if (data.error) throw new Error(data.error);
        if (!data.img_b64) throw new Error('返回数据缺少图片');
        // 替换占位为真实图片
        const wrap = document.createElement('div');
        wrap.className = 'img-fit';
        const img = document.createElement('img');
        img.src = 'data:image/png;base64,' + data.img_b64;
        img.alt = interval + ' K-Line';
        wrap.appendChild(img);
        placeholder.replaceWith(wrap);
        // 更新标题栏 meta
        const meta = card.querySelector('.tf-meta');
        if (meta && data.title_range){
          meta.textContent = data.title_range + '  ·  ' + (data.bars || 0) + ' 根';
        }
        card.removeAttribute('data-pending');
      })
      .catch(err => {
        placeholder.dataset.state = 'error';
        const msg = placeholder.querySelector('.err-msg');
        if (msg) msg.textContent = '⚠ ' + (err.message || '加载失败');
      });
  }

  function enqueue(card){
    if (card.dataset.queued === '1') return;
    card.dataset.queued = '1';
    queue.push(() => fetchTile(card));
    pump();
  }

  // 重试按钮（事件委托）
  document.addEventListener('click', (ev) => {
    const btn = ev.target.closest('.retry-btn');
    if (!btn) return;
    const card = btn.closest('.tf-card');
    if (!card) return;
    // 重置占位状态，重新入队
    const ph = card.querySelector('.tile-placeholder');
    if (ph) ph.dataset.state = 'loading';
    queue.push(() => fetchTile(card));
    pump();
  });

  // IntersectionObserver：底部 200px 提前触发
  const cards = Array.from(document.querySelectorAll('.tf-card[data-pending="1"]'));
  if (cards.length === 0) return;

  if ('IntersectionObserver' in window){
    const observer = new IntersectionObserver((entries) => {
      entries.forEach(en => {
        if (en.isIntersecting){
          enqueue(en.target);
          observer.unobserve(en.target);
        }
      });
    }, { rootMargin: '0px 0px 200px 0px' });
    cards.forEach(c => observer.observe(c));
  } else {
    // 兜底：不支持 IntersectionObserver 时直接全部入队
    cards.forEach(c => enqueue(c));
  }
})();
</script>

</body>
</html>
"""


# ═══════════════════════════════════════════════════════════════════════════
# Flask 路由注册
# ═══════════════════════════════════════════════════════════════════════════

def register_panorama_routes(app):
    """在 app.py 末尾调用：register_panorama_routes(app)"""
    from flask import request, render_template_string, session, redirect, url_for, jsonify

    def _short_sym(market, symbol):
        if market == 'crypto' and symbol.upper().endswith('USDT'):
            return symbol[:-4]
        for sfx in ('.SS', '.SZ', '.HK'):
            if symbol.upper().endswith(sfx):
                return symbol[:-len(sfx)]
        return symbol

    @app.route('/panorama')
    def panorama_page():
        if 'username' not in session:
            return redirect(url_for('login'))

        market       = request.args.get('market', 'crypto')
        symbol       = request.args.get('symbol', '')
        peak_iso     = request.args.get('peak_iso', '')
        s3_start_iso = request.args.get('s3_start_iso', '')
        s3_end_iso   = request.args.get('s3_end_iso', '')
        kind         = request.args.get('kind', 'bullish')
        # 顶层周期：从哪一级开始向下钻取。默认 'weekly' 保证旧 URL 仍能工作。
        top_interval = request.args.get('top_interval', 'weekly')
        # 顶层周期当前的实际时间窗（来自前端 stack[0]，ISO 格式）
        top_start    = request.args.get('top_start', '')
        top_end      = request.args.get('top_end', '')

        if not symbol or not peak_iso:
            return ('<p style="color:#e0e0f0;padding:2rem;font-family:sans-serif">'
                    '参数缺失，请从主界面的 K 线图重新点击全景图按钮。</p>'), 400

        # 校验 top_interval 在该 market 里被支持。
        # 注意 stock 的周期链是 weekly→daily→1h，3day 不在 weekly 链上，但
        # 它是独立的钻取入口（_STOCK_CHAIN 里有 '3day' 键）。所以不能简单地
        # 用 "在 weekly 链里" 来校验。这里直接看 NEXT_INTERVAL_BY_MARKET 是否
        # 有该 market 的该周期键。
        from navigation import NEXT_INTERVAL_BY_MARKET as _NEXT_MAP
        if top_interval not in _NEXT_MAP.get(market, {}):
            return (f'<p style="color:#e0e0f0;padding:2rem;font-family:sans-serif">'
                    f'无效的顶层周期：{top_interval}（market={market}）</p>'), 400

        top_anchor = {
            'peak_iso':        peak_iso,
            's3_start_iso':    s3_start_iso or None,
            's3_end_iso':      s3_end_iso or None,
            'kind':            kind,
            'parent_interval': top_interval,
        }

        tf_results = build_eager(
            market, symbol, top_anchor,
            top_interval=top_interval,
            top_start_iso=top_start or None,
            top_end_iso=top_end or None,
            eager_count=2,   # 首屏：顶层 + 第一张子级
        )

        # 极值时间显示
        try:
            anchor_peak_date = _to_ts(peak_iso).strftime('%Y-%m-%d')
        except Exception:
            anchor_peak_date = peak_iso

        chain = _tf_chain(market, top_interval)

        ctx = dict(
            symbol_short     = _short_sym(market, symbol),
            mk_label         = MK_LABEL.get(market, market),
            anchor_kind      = kind,
            anchor_peak_date = anchor_peak_date,
            # 顶层周期的中文名（用于"信号源：xxx"显示）
            top_iv_label     = IV_LABEL.get(top_interval, top_interval),
            chain            = chain,
            IV_LABEL         = IV_LABEL,
            tf_results       = tf_results,
            generated_at     = _dt.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'),
            # 给 JS 注入：占位卡按需调用 /panorama/tile 时复用这些参数
            ctx_market       = market,
            ctx_symbol       = symbol,
            ctx_peak_iso     = peak_iso,
            ctx_s3_start_iso = s3_start_iso,
            ctx_s3_end_iso   = s3_end_iso,
            ctx_kind         = kind,
            ctx_top_interval = top_interval,
            ctx_top_start    = top_start,
            ctx_top_end      = top_end,
        )
        return render_template_string(PANORAMA_HTML, **ctx)

    @app.route('/panorama/tile')
    def panorama_tile():
        """按需渲染单张周期图。
        参数同 /panorama，外加 interval=要渲染的周期。
        返回 JSON {ok, img_b64, title_range, bars, error}。"""
        if 'username' not in session:
            return jsonify({'ok': False, 'error': 'unauthorized'}), 401

        market       = request.args.get('market', 'crypto')
        symbol       = request.args.get('symbol', '')
        interval     = request.args.get('interval', '')
        peak_iso     = request.args.get('peak_iso', '')
        s3_start_iso = request.args.get('s3_start_iso', '')
        s3_end_iso   = request.args.get('s3_end_iso', '')
        kind         = request.args.get('kind', 'bullish')
        top_interval = request.args.get('top_interval', 'weekly')
        top_start    = request.args.get('top_start', '')
        top_end      = request.args.get('top_end', '')

        if not symbol or not peak_iso or not interval:
            return jsonify({'ok': False, 'error': '缺少必要参数'}), 400

        top_anchor = {
            'peak_iso':        peak_iso,
            's3_start_iso':    s3_start_iso or None,
            's3_end_iso':      s3_end_iso or None,
            'kind':            kind,
            'parent_interval': top_interval,
        }

        result = render_tile(
            market, symbol, interval, top_anchor,
            top_interval=top_interval,
            top_start_iso=top_start or None,
            top_end_iso=top_end or None,
        )
        if result.get('error'):
            return jsonify({'ok': False, 'error': result['error']}), 500
        return jsonify({
            'ok':          True,
            'img_b64':     result['img_b64'],
            'title_range': result['title_range'],
            'bars':        result['bars'],
        })
