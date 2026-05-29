"""
scanner.py — MACD 底背离 / 顶背离批量扫描器
=============================================

路由
----
GET /scanner         扫描配置 + 结果页
GET /scanner/stream  SSE 实时推送进度 + 命中信号
GET /scanner/chart   直接渲染指定代币的 K 线图（含背离标注）

背离类型（signal_type 字段）
----------------------------
  'three_seg' : 标准三段背离，level=1/2/3
  'extreme'   : 极值背离（find_missed_extremes），level=0
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import os
import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

import pandas as pd

import data as _data
import indicator as _ind
from divergence import find_three_segment_divergences, find_missed_extremes
from config import SCANNER_DEFAULT_START


# ═══════════════════════════════════════════════════════════════════════════
# 默认代币列表
# ═══════════════════════════════════════════════════════════════════════════
DEFAULT_CRYPTO_SYMBOLS: list[str] = [
    'BTCUSDT',  'ETHUSDT',  'BNBUSDT',  'SOLUSDT',  'XRPUSDT',
    'ADAUSDT',  'AVAXUSDT', 'DOGEUSDT', 'DOTUSDT',  'LTCUSDT',
    'LINKUSDT', 'UNIUSDT',  'ATOMUSDT', 'XLMUSDT',  'ETCUSDT',
    'FILUSDT',  'VETUSDT',  'ALGOUSDT', 'HBARUSDT',
    'APTUSDT',  'ARBUSDT',  'OPUSDT',   'SUIUSDT',  'INJUSDT',
    'NEARUSDT', 'RNDRUSDT', 'ICPUSDT',  'STXUSDT',  'TIAUSDT',
    'SEIUSDT',  'STRKUSDT', 'EIGENUSDT',
    'AAVEUSDT', 'MKRUSDT',  'LDOUSDT',  'CRVUSDT',  'SNXUSDT',
    'GRTUSDT',  'DYDXUSDT', 'GMXUSDT',  'JUPUSDT',  'ENAUSDT',
    'SANDUSDT', 'MANAUSDT', 'AXSUSDT',  'GALAUSDT', 'APEUSDT',
    'FLOWUSDT', 'IMXUSDT',  'BLURUSDT',
    'RUNEUSDT', 'FTMUSDT',  'EGLDUSDT', 'THETAUSDT','WLDUSDT',
    'NOTUSDT',  'MOVEUSDT',
    'SHIBUSDT', 'PEPEUSDT', 'FLOKIUSDT','BONKUSDT', 'TRUMPUSDT',
    'HYPEUSDT',
]

# 美股（yfinance 风格 ticker，无后缀）
DEFAULT_US_SYMBOLS: list[str] = [
    'AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'TSLA', 'AVGO',
    'AMD',  'NFLX', 'MU',   'INTC',  'QCOM', 'ADBE', 'CRM',  'ORCL',
    'CSCO', 'PLTR', 'COIN', 'MSTR',  'SMCI', 'ARM',  'TSM',  'BABA',
    'JPM',  'BAC',  'V',    'MA',    'DIS',  'BA',   'XOM',  'WMT',
    'COST', 'UNH',  'LLY',  'JNJ',   'PFE',  'NKE',  'UBER', 'SHOP',
    'PYPL', 'SOFI',
]

# A 股（.SS=上交所 6xxxxx / .SZ=深交所 0xxxxx·3xxxxx）
DEFAULT_CN_SYMBOLS: list[str] = [
    '600519.SS', '300750.SZ', '600036.SS', '002594.SZ', '601318.SS',
    '000858.SZ', '600900.SS', '601899.SS', '600276.SS', '000333.SZ',
    '000651.SZ', '600030.SS', '601166.SS', '600887.SS', '002415.SZ',
    '601012.SS', '300059.SZ', '002475.SZ', '600104.SS', '601888.SS',
    '000001.SZ', '600000.SS', '601398.SS', '601988.SS', '600028.SS',
    '601857.SS', '000002.SZ', '002230.SZ', '688981.SS', '688111.SS',
]

# 港股（.HK，4 位代码）
DEFAULT_HK_SYMBOLS: list[str] = [
    '0700.HK', '9988.HK', '3690.HK', '1810.HK', '0388.HK', '0941.HK',
    '1299.HK', '0939.HK', '1398.HK', '2318.HK', '0005.HK', '2628.HK',
    '1024.HK', '9618.HK', '9999.HK', '9888.HK', '2020.HK', '1211.HK',
    '0883.HK', '0857.HK', '0386.HK', '2331.HK', '6618.HK', '0981.HK',
    '1093.HK', '2269.HK', '0291.HK', '2382.HK',
]

# market → 默认标的列表。textarea 留空时按当前 market 取这里的默认。
DEFAULT_SYMBOLS_BY_MARKET: dict[str, list[str]] = {
    'crypto':   DEFAULT_CRYPTO_SYMBOLS,
    'us_stock': DEFAULT_US_SYMBOLS,
    'cn_stock': DEFAULT_CN_SYMBOLS,
    'hk_stock': DEFAULT_HK_SYMBOLS,
}

# 合法 market 白名单（防止前端传入未知值打到数据层）
VALID_MARKETS: frozenset = frozenset(DEFAULT_SYMBOLS_BY_MARKET)


# ═══════════════════════════════════════════════════════════════════════════
# 每用户扫描清单持久化
# ═══════════════════════════════════════════════════════════════════════════
# 扫描器的标的清单按「用户 + 市场」独立保存。每个用户一份
# scanner_symbols_{username}.json，与多用户版 user_settings_{username}.json
# 同款命名风格，存在 scanner.py 所在目录。
#
# 文件结构（只存用户显式保存过的 market，其余沿用内置默认）：
#   { "crypto": ["BTCUSDT", ...], "cn_stock": ["600519.SS", ...] }
#
# 取值优先级：用户保存的该市场清单 → 内置默认列表。
_SCANNER_DIR = os.path.dirname(os.path.abspath(__file__))


def _safe_username(username: str) -> str:
    """清洗用户名用于文件名，防止路径穿越。仅保留字母数字下划线连字符。"""
    cleaned = ''.join(c for c in str(username) if c.isalnum() or c in ('_', '-'))
    return cleaned or 'default'


def _scan_symbols_path(username: str) -> str:
    return os.path.join(_SCANNER_DIR, f'scanner_symbols_{_safe_username(username)}.json')


def _clean_symbol_list(raw) -> list[str]:
    """归一化标的列表：去空白、全大写、去重保序。接受多行字符串或列表。"""
    if isinstance(raw, str):
        items = raw.splitlines()
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        items = []
    seen: set = set()
    out: list[str] = []
    for it in items:
        s = str(it).strip().upper()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def load_user_scan_symbols(username: str) -> dict:
    """读取某用户已保存的扫描清单 {market: [sym,...]}。
    只返回用户显式保存过的 market；文件缺失/损坏 → 返回 {}（调用方退回默认）。"""
    path = _scan_symbols_path(username)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return {}
    out: dict = {}
    for mk in VALID_MARKETS:
        syms = _clean_symbol_list(data.get(mk))
        if syms:
            out[mk] = syms
    return out


def save_user_scan_symbols(username: str, market: str, symbols) -> bool:
    """保存某用户某市场的扫描清单。symbols 为空 → 删除该市场覆盖（恢复内置默认）。
    其它市场的已存清单保持不变。成功返回 True。"""
    if market not in VALID_MARKETS:
        return False
    path = _scan_symbols_path(username)
    data: dict = {}
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
    clean = _clean_symbol_list(symbols)
    if clean:
        data[market] = clean
    else:
        data.pop(market, None)   # 空 = 取消自定义，回退内置默认
    try:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)    # 原子替换，避免写一半被读到
        return True
    except Exception as e:
        print(f"[SCANNER] 保存清单失败 {username}/{market}: {e}", file=sys.stderr)
        return False


def effective_scan_symbols(username: str, market: str) -> list[str]:
    """该用户该市场的有效扫描清单：用户保存的优先，否则内置默认。"""
    saved = load_user_scan_symbols(username).get(market)
    return saved if saved else list(DEFAULT_SYMBOLS_BY_MARKET[market])


# ═══════════════════════════════════════════════════════════════════════════
# 日期工具
# ═══════════════════════════════════════════════════════════════════════════
_INTERVAL_DAYS: dict[str, float] = {
    '15m': 15/1440, '30m': 0.5/24, '1h': 1/24,
    '4h': 4/24, 'daily': 1.0, '3day': 3.0, 'weekly': 7.0,
}
_WARMUP_BARS = 160


def _parse_date(s: str) -> _dt.date:
    s = s.strip()
    for fmt in ('%Y-%m', '%Y-%m-%d', '%d %b, %Y %H:%M:%S', '%d %b, %Y'):
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"无法解析日期: {s!r}")


def _to_data_str(d: _dt.date) -> str:
    """转为 data.get_klines 接受的格式 '1 Apr, 2025'"""
    return d.strftime('%-d %b, %Y')


def _safe_ts(idx, pos: int) -> Optional[pd.Timestamp]:
    try:
        return pd.Timestamp(idx[pos]).tz_localize(None)
    except Exception:
        return None


def _display_base(symbol: str, market: str) -> str:
    """标的的人类可读短名：
    crypto 去掉 USDT 报价后缀；股票去掉交易所后缀（.SS/.SZ/.HK）。"""
    s = symbol.strip()
    if market == 'crypto':
        return s[:-4] if s.upper().endswith('USDT') else s
    up = s.upper()
    for suf in ('.SS', '.SZ', '.HK'):
        if up.endswith(suf):
            return s[:-len(suf)]
    return s


# ═══════════════════════════════════════════════════════════════════════════
# 单币扫描
# ═══════════════════════════════════════════════════════════════════════════
def _scan_one(
    symbol: str, market: str, interval: str,
    fetch_start: str, fetch_end: Optional[str],
    filter_start: pd.Timestamp, filter_end: pd.Timestamp,
    min_bars: int, ratio_threshold: float, max_level: int,
    kind_filter: str, signal_types: set,
) -> dict:
    try:
        df = _data.get_klines(market, symbol, interval, fetch_start, fetch_end)
    except Exception as e:
        return {'symbol': symbol, 'hits': [], 'error': str(e)[:150], 'bars': 0}

    if df is None or len(df) < 30:
        return {'symbol': symbol, 'hits': [], 'error': '数据不足', 'bars': 0}

    try:
        df = _ind.add_indicators(df)
    except Exception as e:
        return {'symbol': symbol, 'hits': [], 'error': f'指标失败: {e}', 'bars': len(df)}

    current_price = float(df['close'].iloc[-1])
    hits = []

    # ── 标准三段背离 ─────────────────────────────────────────────────────
    if 'three_seg' in signal_types:
        try:
            divs = find_three_segment_divergences(
                df['hist'], df['low'], df['high'],
                min_bars=min_bars, ratio_threshold=ratio_threshold,
                max_level=max_level, block_by_opposite=True,
            )
        except Exception:
            divs = []

        for d in divs:
            if kind_filter != 'both' and d['kind'] != kind_filter:
                continue
            signal_ts = _safe_ts(df.index, d['s3_end'])
            s1_ts     = _safe_ts(df.index, d['s1_start'])
            if signal_ts is None or not (filter_start <= signal_ts <= filter_end):
                continue
            hits.append({
                'symbol':        symbol,
                'kind':          d['kind'],
                'level':         d['level'],
                'signal_type':   'three_seg',
                'provisional':   d['provisional'],
                'ratio':         round(d['ratio'], 4),
                'signal_date':   signal_ts.strftime('%Y-%m-%d'),
                's1_date':       s1_ts.strftime('%Y-%m-%d') if s1_ts else '',
                'current_price': current_price,
            })

    # ── 极值背离 ─────────────────────────────────────────────────────────
    if 'extreme' in signal_types:
        try:
            extremes = find_missed_extremes(df['hist'], df['low'], df['high'])
        except Exception:
            extremes = []

        for d in extremes:
            if kind_filter != 'both' and d['kind'] != kind_filter:
                continue
            signal_ts = _safe_ts(df.index, d['s3_end'])
            s1_ts     = _safe_ts(df.index, d['s1_start'])
            if signal_ts is None or not (filter_start <= signal_ts <= filter_end):
                continue
            hits.append({
                'symbol':        symbol,
                'kind':          d['kind'],
                'level':         0,
                'signal_type':   'extreme',
                'provisional':   False,
                'ratio':         None,
                'signal_date':   signal_ts.strftime('%Y-%m-%d'),
                's1_date':       s1_ts.strftime('%Y-%m-%d') if s1_ts else '',
                'current_price': current_price,
            })

    return {'symbol': symbol, 'hits': hits, 'error': None, 'bars': len(df)}


# ═══════════════════════════════════════════════════════════════════════════
# 批量扫描
# ═══════════════════════════════════════════════════════════════════════════
def scan_symbols(
    symbols: list[str], interval: str, start_str: str,
    end_str: Optional[str] = None, market: str = 'crypto',
    max_workers: int = 6, min_bars: int = 2,
    ratio_threshold: float = 0.5, max_level: int = 2,
    kind_filter: str = 'bullish',
    signal_types: Optional[set] = None,
    progress_cb: Optional[Callable] = None,
) -> list[dict]:
    if signal_types is None:
        signal_types = {'three_seg', 'extreme'}

    filter_start = pd.Timestamp(_parse_date(start_str))
    filter_end   = (pd.Timestamp(_parse_date(end_str))
                    if end_str
                    else pd.Timestamp(_dt.date.today() + _dt.timedelta(days=1)))

    days_per_bar = _INTERVAL_DAYS.get(interval, 1.0)
    # 股票只在交易日产生 K 线（节假日/周末无 bar），按自然日往前推会少拉，
    # 导致 MA99/MACD 在 filter_start 处尚未预热稳定。crypto 24/7 连续不需放大。
    warmup_factor = 1.0 if market == 'crypto' else 1.6
    warmup_days  = int(_WARMUP_BARS * days_per_bar * warmup_factor) + 10
    fetch_start  = _to_data_str(_parse_date(start_str) - _dt.timedelta(days=warmup_days))
    fetch_end    = _to_data_str(_parse_date(end_str)) if end_str else None

    all_hits: list[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {
            pool.submit(_scan_one, sym, market, interval,
                        fetch_start, fetch_end, filter_start, filter_end,
                        min_bars, ratio_threshold, max_level,
                        kind_filter, signal_types): sym
            for sym in symbols
        }
        done = 0
        for fut in as_completed(future_map):
            sym = future_map[fut]
            done += 1
            try:
                row = fut.result()
            except Exception as e:
                row = {'symbol': sym, 'hits': [], 'error': str(e), 'bars': 0}
            if progress_cb:
                progress_cb(done, len(symbols), sym, row.get('hits', []))
            all_hits.extend(row.get('hits', []))

    all_hits.sort(key=lambda r: (
        -(int((r.get('signal_date') or '00000000').replace('-', ''))),
        r.get('ratio') if r.get('ratio') is not None else 99.0,
    ))
    return all_hits


# ═══════════════════════════════════════════════════════════════════════════
# K 线图渲染页 HTML
# ═══════════════════════════════════════════════════════════════════════════
CHART_PAGE_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }}</title>
<style>
body { margin:0; background:#12131a; display:flex; flex-direction:column;
       align-items:center; font-family:'Segoe UI',system-ui,sans-serif; }
header { width:100%; display:flex; align-items:center; gap:14px; padding:12px 20px;
         background:#1c1e2b; border-bottom:1px solid #2e3048; }
header a { color:#7878a0; text-decoration:none; font-size:13px; }
header a:hover { color:#d0d0e8; }
header h1 { font-size:16px; font-weight:600; color:#a48af8; }
.chart-wrap { padding:16px; max-width:100%; }
.chart-wrap img { max-width:100%; border-radius:8px;
                  box-shadow:0 4px 24px rgba(0,0,0,.5); }
.err { color:#f87171; padding:40px; font-size:15px; }
</style>
</head>
<body>
<header>
  <a href="/scanner">← 返回扫描器</a>
  <h1>{{ title }}</h1>
</header>
{% if error %}
  <div class="err">⚠ {{ error }}</div>
{% else %}
  <div class="chart-wrap">
    <img src="data:image/png;base64,{{ img_b64 }}" alt="K线图">
  </div>
{% endif %}
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════════════
# 扫描器主页 HTML
# ═══════════════════════════════════════════════════════════════════════════
SCANNER_HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>背离扫描器</title>
<style>
:root {
  --bg:#12131a; --surface:#1c1e2b; --border:#2e3048;
  --accent:#7c6ef0; --accent2:#a48af8; --text:#d0d0e8; --dim:#7878a0;
  --bull:#ff3344; --bear:#22aa44; --warn:#f5a623; --ok:#2dd4bf; --ext:#e879f9;
}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--text);
     font-family:'Segoe UI',system-ui,sans-serif;font-size:14px;min-height:100vh;}
header{display:flex;align-items:center;gap:12px;padding:14px 20px;
       background:var(--surface);border-bottom:1px solid var(--border);}
header h1{font-size:18px;font-weight:600;color:var(--accent2);}
header a{color:var(--dim);text-decoration:none;font-size:13px;}
header a:hover{color:var(--text);}
.layout{display:flex;gap:0;min-height:calc(100vh - 53px);}

/* 左栏 */
.panel{width:300px;flex-shrink:0;background:var(--surface);
       border-right:1px solid var(--border);padding:18px 16px;overflow-y:auto;}
.panel h2{font-size:11px;font-weight:700;color:var(--dim);
           text-transform:uppercase;letter-spacing:.08em;margin-bottom:14px;}
.field{margin-bottom:14px;}
.field label{display:block;font-size:12px;color:var(--dim);margin-bottom:5px;font-weight:500;}
.field input[type=text]{width:100%;padding:7px 10px;background:var(--bg);
  border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px;outline:none;}
.field input[type=text]:focus{border-color:var(--accent);}
.seg-ctrl{display:flex;border-radius:6px;overflow:hidden;border:1px solid var(--border);}
.seg-ctrl label{flex:1;text-align:center;cursor:pointer;padding:7px 2px;
  font-size:11.5px;font-weight:500;background:var(--bg);color:var(--dim);transition:all .15s;}
.seg-ctrl input[type=radio]{display:none;}
.seg-ctrl input[type=checkbox]{display:none;}
.seg-ctrl input[type=radio]:checked+label{background:var(--accent);color:#fff;}
.seg-ctrl input[type=checkbox]:checked+label{background:var(--accent);color:#fff;}
#st-ext:checked+label{background:#7e22ce;}
.toggle-row{display:flex;align-items:center;gap:8px;margin-bottom:14px;}
.toggle-row label{font-size:12px;color:var(--dim);cursor:pointer;user-select:none;}
.toggle-row input[type=checkbox]{
  appearance:none;width:32px;height:18px;border-radius:9px;
  background:var(--border);border:none;cursor:pointer;position:relative;
  transition:background .2s;flex-shrink:0;}
.toggle-row input[type=checkbox]:checked{background:var(--accent);}
.toggle-row input[type=checkbox]::after{
  content:'';position:absolute;width:14px;height:14px;border-radius:50%;
  background:#fff;top:2px;left:2px;transition:left .2s;}
.toggle-row input[type=checkbox]:checked::after{left:16px;}
.sym-textarea{width:100%;background:var(--bg);border:1px solid var(--border);
  border-radius:6px;color:var(--text);font-size:12px;padding:8px 10px;
  font-family:monospace;resize:vertical;min-height:80px;outline:none;}
.sym-textarea:focus{border-color:var(--accent);}
.hint{font-size:11px;color:var(--dim);margin-top:4px;line-height:1.5;}
.btn-scan{width:100%;padding:10px;background:var(--accent);border:none;
  border-radius:8px;color:#fff;font-size:14px;font-weight:600;
  cursor:pointer;transition:opacity .15s;}
.btn-scan:hover{opacity:.85;}
.btn-scan:disabled{opacity:.4;cursor:not-allowed;}
.btn-row{display:flex;gap:8px;margin-top:8px;}
.btn-sm{flex:1;padding:7px 6px;border-radius:6px;border:1px solid var(--border);
  background:var(--bg);color:var(--dim);font-size:12px;font-weight:600;
  cursor:pointer;transition:all .15s;}
.btn-sm:hover{border-color:var(--accent);color:var(--accent2);}
.btn-sm:disabled{opacity:.4;cursor:not-allowed;}
.sym-status{font-size:11px;margin-top:6px;line-height:1.5;}
.sym-status .saved{color:var(--ok);}
.sym-status .deflt{color:var(--dim);}

/* 右栏 */
.results-area{flex:1;padding:18px 20px;overflow-y:auto;
               display:flex;flex-direction:column;gap:12px;}
.progress-wrap{display:none;flex-direction:column;gap:6px;}
.progress-wrap.active{display:flex;}
.progress-bar-bg{height:6px;border-radius:3px;background:var(--border);overflow:hidden;}
.progress-bar-fill{height:100%;border-radius:3px;
  background:linear-gradient(90deg,var(--accent),var(--accent2));
  width:0%;transition:width .3s;}
.progress-text{font-size:12px;color:var(--dim);}
.stats-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;}
.badge{padding:4px 10px;border-radius:20px;font-size:12px;font-weight:600;}
.badge.total{background:rgba(124,110,240,.15);color:var(--accent2);}
.badge.conf {background:rgba(45,212,191,.12); color:var(--ok);}
.badge.prov {background:rgba(30,144,255,.15); color:#5aadf5;}
.badge.ext  {background:rgba(232,121,249,.12);color:var(--ext);}
.result-table-wrap{overflow-x:auto;}
table{width:100%;border-collapse:collapse;min-width:640px;}
th{text-align:left;padding:9px 12px;font-size:10.5px;font-weight:700;color:var(--dim);
   text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid var(--border);
   position:sticky;top:0;background:var(--bg);z-index:1;}
td{padding:8px 12px;font-size:13px;border-bottom:1px solid rgba(46,48,72,.5);}
tr:hover td{background:rgba(255,255,255,.03);}
.sym-link{color:var(--accent2);font-weight:700;text-decoration:none;font-size:14px;}
.sym-link:hover{color:#fff;}
.lv-chip{display:inline-block;padding:2px 7px;border-radius:10px;
         font-size:11px;font-weight:700;white-space:nowrap;}
.lv0 {background:rgba(232,121,249,.18);color:var(--ext);}
.lv1 {background:rgba(255,51,68,.15);  color:var(--bull);}
.lv2 {background:rgba(245,166,35,.15); color:var(--warn);}
.lv3p{background:rgba(45,212,191,.15); color:var(--ok);}
.ratio-wrap{display:flex;align-items:center;gap:6px;}
.ratio-bg{width:50px;height:4px;border-radius:2px;background:var(--border);overflow:hidden;}
.ratio-fill{height:100%;border-radius:2px;
            background:linear-gradient(90deg,var(--bull),var(--warn));}
.empty-msg{text-align:center;color:var(--dim);padding:60px 0;font-size:15px;}
tr.hidden{display:none;}
</style>
</head>
<body>
<header>
  <a href="/">← 返回图表</a>
  <h1>📡 背离扫描器</h1>
</header>
<div class="layout">

<!-- ═══ 左：配置 ═══ -->
<aside class="panel">
  <h2>扫描参数</h2>

  <div class="field">
    <label>扫描市场</label>
    <div class="seg-ctrl">
      <input type="radio" name="market" id="m-crypto" value="crypto" checked>
      <label for="m-crypto">🪙 加密</label>
      <input type="radio" name="market" id="m-us" value="us_stock">
      <label for="m-us">🇺🇸 美股</label>
      <input type="radio" name="market" id="m-cn" value="cn_stock">
      <label for="m-cn">🇨🇳 A股</label>
      <input type="radio" name="market" id="m-hk" value="hk_stock">
      <label for="m-hk">🇭🇰 港股</label>
    </div>
  </div>

  <div class="field">
    <label>扫描方向</label>
    <div class="seg-ctrl">
      <input type="radio" name="kind" id="k-bull" value="bullish" checked>
      <label for="k-bull">📈 底背离</label>
      <input type="radio" name="kind" id="k-bear" value="bearish">
      <label for="k-bear">📉 顶背离</label>
      <input type="radio" name="kind" id="k-both" value="both">
      <label for="k-both">🔄 全部</label>
    </div>
  </div>

  <div class="field">
    <label>信号类型</label>
    <div class="seg-ctrl">
      <input type="checkbox" id="st-3s" checked>
      <label for="st-3s">三段背离</label>
      <input type="checkbox" id="st-ext" checked>
      <label for="st-ext">✦ 极值背离</label>
    </div>
    <div class="hint">极值背离：价格极值落在反向 MACD 段（如 HYPE 5月底背离）</div>
  </div>

  <div class="field">
    <label>K线周期</label>
    <div class="seg-ctrl">
      <input type="radio" name="interval" id="iv-wk" value="weekly">
      <label for="iv-wk">周线</label>
      <input type="radio" name="interval" id="iv-3d" value="3day" checked>
      <label for="iv-3d">3日线</label>
      <input type="radio" name="interval" id="iv-d" value="daily">
      <label for="iv-d">日线</label>
    </div>
  </div>

  <div class="field">
    <label>起始时间（YYYY-MM）</label>
    <input type="text" id="start-str" value="__DEFAULT_START__" placeholder="2025-04">
  </div>

  <div class="field">
    <label>结束时间（留空=今日）</label>
    <input type="text" id="end-str" value="" placeholder="留空 = 当下">
  </div>

  <div class="field" id="field-level">
    <label>三段背离层级</label>
    <div class="seg-ctrl">
      <input type="radio" name="max_level" id="lv1" value="1">
      <label for="lv1">L1 基础</label>
      <input type="radio" name="max_level" id="lv2" value="2" checked>
      <label for="lv2">L2 趋势</label>
      <input type="radio" name="max_level" id="lv3" value="3">
      <label for="lv3">L3 大级</label>
    </div>
  </div>

  <div class="field" id="field-ratio">
    <label>面积比阈值（三段背离）</label>
    <div class="seg-ctrl">
      <input type="radio" name="ratio_thr" id="r40" value="0.4">
      <label for="r40">40%</label>
      <input type="radio" name="ratio_thr" id="r50" value="0.5" checked>
      <label for="r50">50%</label>
      <input type="radio" name="ratio_thr" id="r65" value="0.65">
      <label for="r65">65%</label>
    </div>
  </div>

  <div class="toggle-row">
    <input type="checkbox" id="dedup-toggle" checked>
    <label for="dedup-toggle">每个代币只显示最新信号</label>
  </div>

  <div class="field">
    <label id="sym-label">扫描标的（每行一个）</label>
    <textarea class="sym-textarea" id="symbols-ta"
      placeholder="BTCUSDT&#10;ETHUSDT&#10;HYPEUSDT&#10;..."></textarea>
    <div class="btn-row">
      <button type="button" class="btn-sm" id="btn-save-syms" onclick="saveSymbols()">💾 保存为我的清单</button>
      <button type="button" class="btn-sm" id="btn-reset-syms" onclick="resetSymbols()">↺ 恢复默认</button>
    </div>
    <div class="sym-status" id="sym-status"></div>
    <div class="hint" id="sym-hint">这是你的个人清单，按市场分别保存，互不影响</div>
  </div>

  <button class="btn-scan" id="btn-scan" onclick="startScan()">🔍 开始扫描</button>
</aside>

<!-- ═══ 右：结果 ═══ -->
<main class="results-area" id="results-area">
  <div class="progress-wrap" id="prog-wrap">
    <div class="progress-bar-bg">
      <div class="progress-bar-fill" id="prog-bar"></div>
    </div>
    <div class="progress-text" id="prog-text">准备中…</div>
  </div>

  <div class="stats-row" id="stats-row" style="display:none">
    <span class="badge total" id="b-total">共 0 个信号</span>
    <span class="badge conf"  id="b-conf">0 个已确认</span>
    <span class="badge prov"  id="b-prov">0 个待观察</span>
    <span class="badge ext"   id="b-ext" style="display:none">0 个极值</span>
  </div>

  <div class="result-table-wrap" id="table-wrap" style="display:none">
    <table>
      <thead>
        <tr>
          <th>标的</th>
          <th>信号日期</th>
          <th>类型 / 级别</th>
          <th>状态</th>
          <th>面积比</th>
          <th>结构起点</th>
          <th>当前价</th>
        </tr>
      </thead>
      <tbody id="result-tbody"></tbody>
    </table>
  </div>

  <div class="empty-msg" id="empty-msg">设置参数后点击「开始扫描」</div>
</main>
</div>

<script>
let es = null;
let extCount=0, provCount=0, confCount=0, totalCount=0;
// symbol → 该代币在 tbody 中第一条 tr（最新信号），用于去重隐藏
const symLatest = {};

// 各市场的输入提示 / 显示规则。currency 用于结果表「当前价」前缀。
const MARKET_META = {
  crypto:   { ph: 'BTCUSDT\nETHUSDT\nHYPEUSDT\n...',        cur: '$' },
  us_stock: { ph: 'AAPL\nNVDA\nTSLA\n...',                  cur: '$' },
  cn_stock: { ph: '600519.SS\n300750.SZ\n000858.SZ\n...',   cur: '¥' },
  hk_stock: { ph: '0700.HK\n9988.HK\n3690.HK\n...',         cur: 'HK$' },
};

function currentMarket() { return getRadio('market') || 'crypto'; }

let _symBusy = false;
function setSymStatus(html) { document.getElementById('sym-status').innerHTML = html; }

// 切换市场 / 进入页面：加载该用户在此市场保存的清单（无则内置默认）
async function loadSymbols(market) {
  const ta = document.getElementById('symbols-ta');
  ta.placeholder = (MARKET_META[market] || MARKET_META.crypto).ph;
  setSymStatus('<span class="deflt">载入中…</span>');
  try {
    const r = await fetch('/scanner/symbols?market=' + encodeURIComponent(market));
    const d = await r.json();
    const syms = d.symbols || [];
    ta.value = syms.join('\n');
    if (d.is_custom)
      setSymStatus(`<span class="saved">● 已保存为你的清单（${syms.length} 个）</span>`);
    else
      setSymStatus(`<span class="deflt">○ 内置默认（${syms.length} 个）· 编辑后点「保存」即成为你的专属清单</span>`);
  } catch (e) {
    ta.value = '';
    setSymStatus('<span class="deflt">载入失败，可手动填写后保存</span>');
  }
}

async function saveSymbols() {
  if (_symBusy) return;  _symBusy = true;
  const market = currentMarket();
  const raw = document.getElementById('symbols-ta').value;
  const btn = document.getElementById('btn-save-syms');
  const old = btn.textContent; btn.disabled = true; btn.textContent = '保存中…';
  try {
    const r = await fetch('/scanner/symbols', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ market, symbols: raw }),
    });
    const d = await r.json();
    if (d.ok) {
      const syms = d.symbols || [];
      document.getElementById('symbols-ta').value = syms.join('\n');
      if (d.is_custom)
        setSymStatus(`<span class="saved">✓ 已保存为你的清单（${syms.length} 个）</span>`);
      else
        setSymStatus(`<span class="deflt">○ 清单为空，已回退内置默认（${syms.length} 个）</span>`);
    } else {
      setSymStatus('<span class="deflt">保存失败</span>');
    }
  } catch (e) {
    setSymStatus('<span class="deflt">保存失败</span>');
  } finally {
    btn.disabled = false; btn.textContent = old; _symBusy = false;
  }
}

async function resetSymbols() {
  if (_symBusy) return;
  if (!confirm('恢复该市场的内置默认清单？你为此市场保存的自定义清单将被清除。')) return;
  _symBusy = true;
  const market = currentMarket();
  const btn = document.getElementById('btn-reset-syms');
  const old = btn.textContent; btn.disabled = true; btn.textContent = '恢复中…';
  try {
    const r = await fetch('/scanner/symbols', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ market, action: 'reset' }),
    });
    const d = await r.json();
    const syms = d.symbols || [];
    document.getElementById('symbols-ta').value = syms.join('\n');
    setSymStatus(`<span class="deflt">○ 已恢复内置默认（${syms.length} 个）</span>`);
  } catch (e) {
    setSymStatus('<span class="deflt">操作失败</span>');
  } finally {
    btn.disabled = false; btn.textContent = old; _symBusy = false;
  }
}

document.querySelectorAll('[name="market"]').forEach(el =>
  el.addEventListener('change', () => loadSymbols(currentMarket())));

// 首次进入页面：加载当前市场的清单
loadSymbols(currentMarket());

document.getElementById('dedup-toggle').addEventListener('change', applyDedup);

function applyDedup() {
  const dedup = document.getElementById('dedup-toggle').checked;
  // 重新根据 dedup 显示/隐藏重复行
  const rows = document.querySelectorAll('#result-tbody tr[data-sym]');
  const seen = new Set();
  // tbody 按最新优先插入（insertBefore firstChild），故遍历顺序就是从新到旧
  rows.forEach(tr => {
    const sym = tr.dataset.sym;
    if (dedup && seen.has(sym)) {
      tr.classList.add('hidden');
    } else {
      tr.classList.remove('hidden');
      seen.add(sym);
    }
  });
}

document.querySelectorAll('#st-3s,#st-ext').forEach(el => {
  el.addEventListener('change', () => {
    const only3s = document.getElementById('st-3s').checked;
    document.getElementById('field-level').style.opacity = only3s ? '1' : '.4';
    document.getElementById('field-ratio').style.opacity = only3s ? '1' : '.4';
  });
});

function getRadio(name) {
  for (const el of document.querySelectorAll(`[name="${name}"]`))
    if (el.checked) return el.value;
  return '';
}

function startScan() {
  if (es) { es.close(); es = null; }
  const do3s  = document.getElementById('st-3s').checked;
  const doExt = document.getElementById('st-ext').checked;
  if (!do3s && !doExt) { alert('请至少选择一种信号类型'); return; }

  const kind     = getRadio('kind');
  const market   = getRadio('market');
  const interval = getRadio('interval');
  const startStr = document.getElementById('start-str').value.trim();
  const endStr   = document.getElementById('end-str').value.trim();
  const maxLevel = getRadio('max_level');
  const ratioThr = getRadio('ratio_thr');
  const symRaw   = document.getElementById('symbols-ta').value.trim();
  if (!startStr) { alert('请填写起始时间'); return; }

  // 重置
  document.getElementById('btn-scan').disabled = true;
  document.getElementById('empty-msg').style.display = 'none';
  document.getElementById('stats-row').style.display = 'none';
  document.getElementById('table-wrap').style.display = 'none';
  document.getElementById('result-tbody').innerHTML = '';
  document.getElementById('prog-wrap').classList.add('active');
  document.getElementById('prog-bar').style.width = '0%';
  document.getElementById('prog-text').textContent = '连接中…';
  extCount=provCount=confCount=totalCount=0;
  Object.keys(symLatest).forEach(k => delete symLatest[k]);

  const params = new URLSearchParams({
    kind, market, interval, start: startStr, end: endStr,
    max_level: maxLevel, ratio_thr: ratioThr, symbols: symRaw,
    do_three_seg: do3s?'1':'0', do_extreme: doExt?'1':'0',
  });
  // 将当前扫描参数存到全局，供 appendRow 构建图表链接
  window._scanParams = { market, interval, startStr, endStr };

  es = new EventSource('/scanner/stream?' + params.toString());
  es.onmessage = function(e) {
    let msg; try { msg = JSON.parse(e.data); } catch { return; }
    if (msg.type === 'progress') {
      const pct = Math.round(msg.done/msg.total*100);
      document.getElementById('prog-bar').style.width = pct+'%';
      document.getElementById('prog-text').textContent =
        `扫描中 ${msg.done}/${msg.total}：${msg.symbol}`;
    } else if (msg.type === 'hit') {
      totalCount++;
      const h = msg.hit;
      if (h.signal_type==='extreme') extCount++;
      else if (h.provisional) provCount++; else confCount++;
      appendRow(h);
      updateStats();
    } else if (msg.type === 'done') {
      es.close(); es=null;
      document.getElementById('btn-scan').disabled = false;
      document.getElementById('prog-wrap').classList.remove('active');
      document.getElementById('stats-row').style.display = 'flex';
      if (totalCount===0) {
        document.getElementById('empty-msg').style.display='block';
        document.getElementById('empty-msg').textContent='⚠ 指定时间段内未发现符合条件的背离信号';
      }
    }
  };
  es.onerror = function() {
    if (es) { es.close(); es=null; }
    document.getElementById('btn-scan').disabled = false;
    document.getElementById('prog-wrap').classList.remove('active');
  };
}

function updateStats() {
  document.getElementById('b-total').textContent = `共 ${totalCount} 个信号`;
  document.getElementById('b-conf').textContent  = `${confCount} 个已确认`;
  document.getElementById('b-prov').textContent  = `${provCount} 个待观察`;
  const bExt = document.getElementById('b-ext');
  if (extCount>0) { bExt.style.display=''; bExt.textContent=`${extCount} 个极值`; }
  document.getElementById('stats-row').style.display = 'flex';
}

function displayBase(sym, market) {
  if (market === 'crypto')
    return sym.toUpperCase().endsWith('USDT') ? sym.slice(0,-4) : sym;
  return sym.replace(/\.(SS|SZ|HK)$/i, '');
}

function appendRow(h) {
  document.getElementById('table-wrap').style.display = 'block';
  const sym  = h.symbol;
  const p    = window._scanParams || {};
  const mkt  = p.market || 'crypto';
  const base = displayBase(sym, mkt);
  const icon = h.kind==='bullish' ? '📈' : '📉';

  // ── 图表链接：直接渲染 K 线图 ──────────────────────────────────────────
  // start 用 s1_date（结构起点），让图表完整展示背离结构
  // end 用 endStr（扫描结束），展示后续走势
  const chartUrl = '/scanner/chart?' + new URLSearchParams({
    symbol:   sym,
    market:   mkt,
    interval: p.interval || '3day',
    start:    h.s1_date || p.startStr || '',
    end:      p.endStr  || '',
  }).toString();

  // 类型+级别
  let lvHtml;
  if (h.signal_type==='extreme') {
    lvHtml = '<span class="lv-chip lv0">✦ 极值</span>';
  } else {
    const lv  = h.level;
    const cls = lv<=1 ? 'lv1' : lv===2 ? 'lv2' : 'lv3p';
    lvHtml = `<span class="lv-chip ${cls}">L${lv}</span>`;
  }

  // 状态
  let stHtml;
  if      (h.signal_type==='extreme') stHtml = '<span style="color:var(--ext)">✓ 已确认</span>';
  else if (h.provisional)             stHtml = '<span style="color:#5aadf5">⏳ 待完成</span>';
  else                                stHtml = '<span style="color:var(--ok)">✓ 已确认</span>';

  // 面积比
  let ratHtml;
  if (h.ratio==null) {
    ratHtml = '<span style="color:var(--dim);font-size:11px">— 无</span>';
  } else {
    const fw = Math.max(4, Math.round(h.ratio*100));
    ratHtml = `<div class="ratio-wrap">
      <div class="ratio-bg"><div class="ratio-fill" style="width:${fw}%"></div></div>
      <span>${(h.ratio*100).toFixed(1)}%</span></div>`;
  }

  const cur = (MARKET_META[mkt] || MARKET_META.crypto).cur;
  const price = h.current_price
    ? cur+h.current_price.toLocaleString(undefined,
        {minimumFractionDigits:2,maximumFractionDigits:4})
    : '-';

  const tr = document.createElement('tr');
  tr.dataset.sym = sym;   // 用于去重逻辑
  tr.innerHTML = `
    <td><a class="sym-link" href="${chartUrl}" target="_blank">${icon} ${base}</a></td>
    <td>${h.signal_date||'-'}</td>
    <td>${lvHtml}</td>
    <td>${stHtml}</td>
    <td>${ratHtml}</td>
    <td style="color:var(--dim);font-size:12px">${h.s1_date||'-'}</td>
    <td style="font-variant-numeric:tabular-nums">${price}</td>`;

  const tbody = document.getElementById('result-tbody');
  tbody.insertBefore(tr, tbody.firstChild);

  // 去重：若该 symbol 已经有更新的行，隐藏本行
  if (document.getElementById('dedup-toggle').checked) {
    if (symLatest[sym]) {
      // 新插入的行（insertBefore firstChild）比已有行更新，隐藏旧行
      symLatest[sym].classList.add('hidden');
    }
    symLatest[sym] = tr;  // 记录最新行
  }
}
</script>
</body>
</html>
"""


# ═══════════════════════════════════════════════════════════════════════════
# Flask 路由注册
# ═══════════════════════════════════════════════════════════════════════════
def register_scanner_routes(app):
    """在 app.py 末尾调用：register_scanner_routes(app)"""
    import base64
    import time as _time
    from flask import (request, session, redirect, url_for, jsonify,
                       Response, stream_with_context, render_template_string)

    @app.route('/scanner')
    def scanner_page():
        if 'username' not in session:
            return redirect(url_for('login'))
        return SCANNER_HTML.replace('__DEFAULT_START__', SCANNER_DEFAULT_START)

    # ── /scanner/symbols：读取 / 保存当前用户的扫描清单 ──────────────────
    @app.route('/scanner/symbols', methods=['GET'])
    def scanner_symbols_get():
        if 'username' not in session:
            return jsonify({'error': 'unauthorized'}), 401
        market = request.args.get('market', 'crypto')
        if market not in VALID_MARKETS:
            market = 'crypto'
        saved = load_user_scan_symbols(session['username']).get(market)
        return jsonify({
            'market':        market,
            'symbols':       saved if saved else list(DEFAULT_SYMBOLS_BY_MARKET[market]),
            'is_custom':     bool(saved),
            'default_count': len(DEFAULT_SYMBOLS_BY_MARKET[market]),
        })

    @app.route('/scanner/symbols', methods=['POST'])
    def scanner_symbols_post():
        if 'username' not in session:
            return jsonify({'ok': False, 'error': 'unauthorized'}), 401
        body   = request.get_json(silent=True) or {}
        market = body.get('market', 'crypto')
        action = body.get('action', 'save')
        if market not in VALID_MARKETS:
            return jsonify({'ok': False, 'error': 'bad market'}), 400

        if action == 'reset':
            # 删除该市场的自定义覆盖，恢复内置默认
            save_user_scan_symbols(session['username'], market, [])
        else:
            save_user_scan_symbols(session['username'], market,
                                   body.get('symbols', ''))

        saved = load_user_scan_symbols(session['username']).get(market)
        return jsonify({
            'ok':        True,
            'market':    market,
            'symbols':   saved if saved else list(DEFAULT_SYMBOLS_BY_MARKET[market]),
            'is_custom': bool(saved),
        })

    # ── /scanner/chart：直接渲染 K 线图 ──────────────────────────────────
    @app.route('/scanner/chart')
    def scanner_chart():
        if 'username' not in session:
            return redirect(url_for('login'))

        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from plot_kline import render_chart

        symbol   = request.args.get('symbol',   'BTCUSDT')
        market   = request.args.get('market',   'crypto')
        interval = request.args.get('interval', '3day')
        start    = request.args.get('start',    '').strip()
        end      = request.args.get('end',      '').strip() or None
        if market not in VALID_MARKETS:
            market = 'crypto'

        # start 格式：'YYYY-MM-DD' → 转为 data 层接受的格式
        def _fmt(s):
            if not s:
                return None
            try:
                d = _parse_date(s)
                return _to_data_str(d)
            except Exception:
                return s

        start_str = _fmt(start)
        end_str   = _fmt(end)

        base = _display_base(symbol, market)
        title = f"{base} {interval} K线图"

        try:
            fig, df, divs = render_chart(
                market, symbol, interval,
                start_str, end_str,
            )
            buf = io.BytesIO()
            fig.savefig(buf, format='png', bbox_inches='tight', pad_inches=0.5,
                        dpi=130)
            plt.close(fig)
            buf.seek(0)
            img_b64 = base64.b64encode(buf.read()).decode('utf-8')
            return render_template_string(
                CHART_PAGE_HTML, title=title, img_b64=img_b64, error=None)
        except Exception as e:
            return render_template_string(
                CHART_PAGE_HTML, title=title, img_b64=None, error=str(e))

    # ── /scanner/stream：SSE 实时扫描 ────────────────────────────────────
    @app.route('/scanner/stream')
    def scanner_stream():
        if 'username' not in session:
            return Response('data: {"type":"error","msg":"未登录"}\n\n',
                            mimetype='text/event-stream')

        kind      = request.args.get('kind',        'bullish')
        market    = request.args.get('market',      'crypto')
        interval  = request.args.get('interval',    '3day')
        start_str = request.args.get('start', SCANNER_DEFAULT_START)
        end_str   = request.args.get('end',         '') or None
        max_level = int(request.args.get('max_level', 2))
        ratio_thr = float(request.args.get('ratio_thr', 0.5))
        sym_raw   = request.args.get('symbols',     '').strip()
        do_3s     = request.args.get('do_three_seg','1') == '1'
        do_ext    = request.args.get('do_extreme',  '1') == '1'

        if market not in VALID_MARKETS:
            market = 'crypto'

        signal_types = set()
        if do_3s:  signal_types.add('three_seg')
        if do_ext: signal_types.add('extreme')
        if not signal_types:
            signal_types = {'three_seg', 'extreme'}

        # textarea 留空 → 用该用户保存的清单；没有再退回内置默认
        symbols = (_clean_symbol_list(sym_raw)
                   if sym_raw else effective_scan_symbols(session['username'], market))

        # 股票数据源（yfinance / akshare）并发过高易被限流，降并发更稳。
        max_workers = 6 if market == 'crypto' else 4

        hit_q   = queue.Queue()
        prog_q  = queue.Queue()
        done_ev = threading.Event()

        def _cb(done, total, sym, hits):
            prog_q.put((done, total, sym))
            for h in hits:
                hit_q.put(h)

        def _run():
            try:
                scan_symbols(
                    symbols=symbols, interval=interval,
                    start_str=start_str, end_str=end_str,
                    market=market, max_workers=max_workers,
                    min_bars=2, ratio_threshold=ratio_thr,
                    max_level=max_level, kind_filter=kind,
                    signal_types=signal_types, progress_cb=_cb,
                )
            except Exception as e:
                print(f"[SCANNER] {e}", file=sys.stderr)
            finally:
                done_ev.set()

        threading.Thread(target=_run, daemon=True).start()

        @stream_with_context
        def generate():
            total = len(symbols)
            yield f"data: {json.dumps({'type':'progress','done':0,'total':total,'symbol':''})}\n\n"
            while not done_ev.is_set() or not prog_q.empty() or not hit_q.empty():
                while not prog_q.empty():
                    try:
                        d, t, s = prog_q.get_nowait()
                        yield f"data: {json.dumps({'type':'progress','done':d,'total':t,'symbol':s})}\n\n"
                    except queue.Empty:
                        break
                while not hit_q.empty():
                    try:
                        h = hit_q.get_nowait()
                        yield f"data: {json.dumps({'type':'hit','hit':h})}\n\n"
                    except queue.Empty:
                        break
                if done_ev.is_set():
                    break
                _time.sleep(0.1)
            # 清空残余
            while not prog_q.empty():
                try:
                    d, t, s = prog_q.get_nowait()
                    yield f"data: {json.dumps({'type':'progress','done':d,'total':t,'symbol':s})}\n\n"
                except queue.Empty:
                    break
            while not hit_q.empty():
                try:
                    h = hit_q.get_nowait()
                    yield f"data: {json.dumps({'type':'hit','hit':h})}\n\n"
                except queue.Empty:
                    break
            yield f"data: {json.dumps({'type':'done'})}\n\n"

        return Response(
            generate(), mimetype='text/event-stream',
            headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'},
        )
