"""
用户设置持久化（多 market 版 - v4）
=================================
两个 UI（main.py 桌面端、app.py Web 端）共享同一份 user_settings.json，
存放在脚本所在目录。

字段结构（v4，四 market + 标的中文名）
--------------------------------------
  language : 'en' | 'zh'
  market   : 'crypto' | 'us_stock' | 'cn_stock' | 'hk_stock'   ← 当前激活的 market
  crypto   : { symbols, ranges }
  us_stock : { symbols, ranges }
  cn_stock : { symbols, ranges }
  hk_stock : { symbols, ranges }

每个 market 块内：
  symbols : [{'ticker': 'BTCUSDT', 'cn_name': 'BTC'}, ...]   ← v4 新结构
  ranges  : {iv: [{'label':..., 'start':..., 'end':...}, ...]}

cn_name 是用户自定义的中文显示名。留空字符串表示"用 fallback"
（plot_kline 内置表 → 派生短码）。

入口周期由 ENTRY_INTERVALS_BY_MARKET 定义：
  - crypto  : ('weekly', '3day', 'daily')   币圈 24/7 连续
  - us_stock: ('weekly', '3day', 'daily')
  - cn_stock: ('weekly', '3day', 'daily')
  - hk_stock: ('weekly', '3day', 'daily')

stock 的 3day 没有数据源原生粒度，由 adapter（yfinance / akshare）按
daily 拉取后重采样合成（3 自然日一根，origin='epoch' 固定对齐）。

钻取（next-level）的链条由 navigation.NEXT_INTERVAL_BY_MARKET 维护。

向后兼容（自动迁移）
--------------------
  v1 (无 market 字段，symbols/ranges 直接放顶层)
    → 顶层视为 crypto 的，其它 market 用默认值

  v2 (含 'crypto' 和 'stock' 两个块)
    → 'stock' 改名为 'us_stock'，cn_stock/hk_stock 用默认值

  v3 (symbols 是字符串数组 ['BTCUSDT', ...])
    → 自动包成 [{ticker, cn_name}, ...]，cn_name 优先从 plot_kline
      内置表填充（已知 ticker 直接获得"茅台/腾讯/美光"等中文名），
      未知 ticker 的 cn_name 留空字符串

  v4 (symbols 是 dict 数组 [{ticker, cn_name}, ...])
    → 直接使用

文件不存在或解析出错时回退到默认值，永不向调用方抛异常。
保存失败（磁盘只读等）会抛异常，由调用方负责提示用户。
"""
import datetime as _dt
import json
import os

_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(_DIR, 'user_settings.json')


# ── 当前支持的 markets。顺序即 UI 上的展示顺序 ──────────────────────
# 'us_stock' / 'cn_stock' / 'hk_stock' 三个 stock market 共用 yfinance
# adapter，区分点是 ticker 后缀（无后缀 → 美股；.SS/.SZ → A 股；.HK → 港股）。
# 后缀由 ticker 自己携带，data.py 路由层只看 market 字段做 adapter 选择。
MARKETS = ('crypto', 'us_stock', 'cn_stock', 'hk_stock')

# ── 每个 market 的入口周期 ──────────────────────────────────────────
# 改这里即影响 UI 上能选的入口和 settings 的 ranges 字典结构；
# navigation 模块的钻取链条独立维护。
ENTRY_INTERVALS_BY_MARKET = {
    'crypto':   ('weekly', '3day', 'daily'),
    'us_stock': ('weekly', '3day', 'daily'),
    'cn_stock': ('weekly', '3day', 'daily'),
    'hk_stock': ('weekly', '3day', 'daily'),
}

# 旧代码可能直接 import ENTRY_INTERVALS（指 crypto 的入口）。保留作为
# 兼容别名，新代码请用 get_entry_intervals(market)。
ENTRY_INTERVALS = ENTRY_INTERVALS_BY_MARKET['crypto']


def get_entry_intervals(market):
    """返回指定 market 的入口周期 tuple。"""
    if market not in ENTRY_INTERVALS_BY_MARKET:
        raise ValueError(f"未知 market: {market!r}")
    return ENTRY_INTERVALS_BY_MARKET[market]


# ── 工厂默认值 ────────────────────────────────────────────────────
# 改这里只影响"全新用户 / 删过 user_settings.json 的用户"。
DEFAULT_LANGUAGE = 'zh'
DEFAULT_MARKET   = 'crypto'

# 默认标的菜单
# ─────────────
# 新用户首次启动 / 删过 user_settings.json 的用户首次启动时,会用这份默认值
# 填进 settings。一旦用户保存过设置,这份默认值就不再被读取。
#
# 数据源:plot_kline.SYMBOL_CONFIG_BY_MARKET 是绘图层维护的"ticker → {short,
# cn_name}"权威字典(图表标题、文件名、按钮显示都要查它),本默认菜单完全
# 派生自它,避免两处硬编码的同步问题。在 plot_kline 加新标的会自动出现在
# 新用户菜单里。
#
# 派生时机:懒加载——plot_kline 依赖 matplotlib / data / indicator 等重型
# 模块,在 settings 模块顶部直接 import 会让任何用 settings 的进程都被迫
# 加载它们(还可能触发循环依赖)。改成函数后只在真正构造默认 block 时才
# 触发 import,与本文件下方 _lookup_builtin_cn_name 同款思路。
def _default_symbols_for_market(market):
    """从 plot_kline.SYMBOL_CONFIG_BY_MARKET 派生该 market 的默认标的菜单。

    返回 [{'ticker', 'cn_name'}, ...]——顺序即 plot_kline 配置表里的顺序
    (Python 3.7+ dict 保序)。plot_kline 加载失败时退回空列表,调用方按需兜底。
    """
    try:
        from plot_kline import SYMBOL_CONFIG_BY_MARKET
    except Exception:
        return []
    return [{'ticker': ticker, 'cn_name': cfg.get('cn_name', '')}
            for ticker, cfg in SYMBOL_CONFIG_BY_MARKET.get(market, {}).items()]

# 每个 market × 每个入口周期的默认时段。
DEFAULT_RANGES_BY_MARKET = {
    'crypto': {
        'weekly': [
            {'label': '2017-08 ~ 2020-05', 'start': '17 Aug, 2017', 'end': '30 May, 2020'},
            {'label': '2020-03 ~ 2022-12', 'start': '17 Mar, 2020', 'end': '30 Dec, 2022'},
            {'label': '2022-10 ~ 2025-10', 'start': '17 Oct, 2022', 'end': '30 Oct, 2025'},
            {'label': '2025-04 ~ 2027-11', 'start': '17 Apr, 2025', 'end': '30 Nov, 2027'},
        ],
        '3day': [
            {'label': '2017-08 ~ 2020-05', 'start': '17 Aug, 2017', 'end': '30 May, 2020'},
            {'label': '2020-03 ~ 2022-12', 'start': '17 Mar, 2020', 'end': '30 Dec, 2022'},
            {'label': '2022-10 ~ 2025-10', 'start': '17 Oct, 2022', 'end': '30 Oct, 2025'},
            {'label': '2025-04 ~ 2027-11', 'start': '17 Apr, 2025', 'end': '30 Nov, 2027'},
        ],
        'daily': [
            {'label': '2017-08 ~ 2020-05', 'start': '17 Aug, 2017', 'end': '30 May, 2020'},
            {'label': '2020-03 ~ 2022-12', 'start': '17 Mar, 2020', 'end': '30 Dec, 2022'},
            {'label': '2022-10 ~ 2025-10', 'start': '17 Oct, 2022', 'end': '30 Oct, 2025'},
            {'label': '2025-04 ~ 2027-11', 'start': '17 Apr, 2025', 'end': '30 Nov, 2027'},
        ],
    },
    'us_stock': {
        # 美股 weekly：~3 年覆盖一个完整牛熊节奏
        'weekly': [
            {'label': '2021-01 ~ 2023-04', 'start': '01 Jan, 2021', 'end': '01 Apr, 2023'},
            {'label': '2023-01 ~ 2025-04', 'start': '01 Jan, 2023', 'end': '01 Apr, 2025'},
            {'label': '2025-01 ~ 2027-01', 'start': '01 Jan, 2025', 'end': '01 Jan, 2027'},
        ],
        # 美股 3day：~1 年（约 120 根 3 日 K，与 daily 一屏密度相当）
        '3day': [
            {'label': '2021-01 ~ 2023-04', 'start': '01 Jan, 2021', 'end': '01 Apr, 2023'},
            {'label': '2023-01 ~ 2025-04', 'start': '01 Jan, 2023', 'end': '01 Apr, 2025'},
            {'label': '2025-01 ~ 2027-01', 'start': '01 Jan, 2025', 'end': '01 Jan, 2027'},
        ],
        # 美股 daily：~6 月（约 120 个交易日）
        'daily': [
            {'label': '2021-01 ~ 2023-04', 'start': '01 Jan, 2021', 'end': '01 Apr, 2023'},
            {'label': '2023-01 ~ 2025-04', 'start': '01 Jan, 2023', 'end': '01 Apr, 2025'},
            {'label': '2025-01 ~ 2027-01', 'start': '01 Jan, 2025', 'end': '01 Jan, 2027'},
        ],
    },
    'cn_stock': {
        # A 股 weekly：覆盖典型牛熊周期（2014-2015 杠杆牛、2018 熊、2021 茅台顶、
        # 2024 反转）。锚点参照 A 股的实际节奏，跟美股错开
        'weekly': [
            {'label': '2021-01 ~ 2023-04', 'start': '01 Jan, 2021', 'end': '01 Apr, 2023'},
            {'label': '2023-01 ~ 2025-04', 'start': '01 Jan, 2023', 'end': '01 Apr, 2025'},
            {'label': '2025-01 ~ 2027-01', 'start': '01 Jan, 2025', 'end': '01 Jan, 2027'},
        ],
        # A 股 3day：~1 年（约 120 根 3 日 K，与 daily 一屏密度相当）
        '3day': [
            {'label': '2021-01 ~ 2023-04', 'start': '01 Jan, 2021', 'end': '01 Apr, 2023'},
            {'label': '2023-01 ~ 2025-04', 'start': '01 Jan, 2023', 'end': '01 Apr, 2025'},
            {'label': '2025-01 ~ 2027-01', 'start': '01 Jan, 2025', 'end': '01 Jan, 2027'},
        ],
        # A 股 daily：~6 月（约 120 个交易日）
        'daily': [
            {'label': '2021-01 ~ 2023-04', 'start': '01 Jan, 2021', 'end': '01 Apr, 2023'},
            {'label': '2023-01 ~ 2025-04', 'start': '01 Jan, 2023', 'end': '01 Apr, 2025'},
            {'label': '2025-01 ~ 2027-01', 'start': '01 Jan, 2025', 'end': '01 Jan, 2027'},
        ],
    },
    'hk_stock': {
        # 港股 weekly：港股节奏比 A 股更国际化但有自己的特点
        # （2018 顶 / 2020 底 / 2021 互联网监管熊 / 2024-2025 反转）
        'weekly': [
            {'label': '2021-01 ~ 2023-04', 'start': '01 Jan, 2021', 'end': '01 Apr, 2023'},
            {'label': '2023-01 ~ 2025-04', 'start': '01 Jan, 2023', 'end': '01 Apr, 2025'},
            {'label': '2025-01 ~ 2027-01', 'start': '01 Jan, 2025', 'end': '01 Jan, 2027'},
        ],
        # 港股 3day：~1 年（约 120 根 3 日 K，与 daily 一屏密度相当）
        '3day': [
            {'label': '2021-01 ~ 2023-04', 'start': '01 Jan, 2021', 'end': '01 Apr, 2023'},
            {'label': '2023-01 ~ 2025-04', 'start': '01 Jan, 2023', 'end': '01 Apr, 2025'},
            {'label': '2025-01 ~ 2027-01', 'start': '01 Jan, 2025', 'end': '01 Jan, 2027'},
        ],
        'daily': [
            {'label': '2021-01 ~ 2023-04', 'start': '01 Jan, 2021', 'end': '01 Apr, 2023'},
            {'label': '2023-01 ~ 2025-04', 'start': '01 Jan, 2023', 'end': '01 Apr, 2025'},
            {'label': '2025-01 ~ 2027-01', 'start': '01 Jan, 2025', 'end': '01 Jan, 2027'},
        ],
    },
}


def _default_market_block(market):
    """返回某个 market 的默认 {symbols, ranges} 子结构（深拷贝）。

    symbols 从 plot_kline 内置表派生(v4 dict 数组)。
    """
    return {
        'symbols': _default_symbols_for_market(market),
        'ranges': {
            iv: [dict(r) for r in DEFAULT_RANGES_BY_MARKET[market].get(iv, [])]
            for iv in ENTRY_INTERVALS_BY_MARKET[market]
        },
    }


def _defaults():
    """每次返回独立副本，避免外部 in-place 修改污染默认值。"""
    out = {
        'language': DEFAULT_LANGUAGE,
        'market':   DEFAULT_MARKET,
    }
    for mk in MARKETS:
        out[mk] = _default_market_block(mk)
    return out


def _clean_range_list(rngs):
    """过滤一个 list，只保留 label/start/end 三字段齐全的字典。"""
    if not isinstance(rngs, list):
        return []
    clean = []
    for r in rngs:
        if (isinstance(r, dict)
                and r.get('label') and r.get('start') and r.get('end')):
            clean.append({
                'label': str(r['label']),
                'start': str(r['start']),
                'end':   str(r['end']),
            })
    return clean


def _normalize_ranges_for_market(raw, market):
    """
    把磁盘读到的 ranges 字段规整为 {iv: list[range], ...}，
    只保留该 market 允许的 interval 键。
    """
    allowed = ENTRY_INTERVALS_BY_MARKET[market]
    out = {iv: [] for iv in allowed}

    if isinstance(raw, list):
        # 极旧的 v1 格式：整个列表当作 weekly（仅在迁移路径上成立）
        if 'weekly' in allowed:
            out['weekly'] = _clean_range_list(raw)
    elif isinstance(raw, dict):
        for iv in allowed:
            out[iv] = _clean_range_list(raw.get(iv))

    # 任一入口为空 → 用该 market 的默认值兜底
    for iv in allowed:
        if not out[iv]:
            out[iv] = [dict(r) for r in DEFAULT_RANGES_BY_MARKET[market].get(iv, [])]

    return out


def _lookup_builtin_cn_name(market, ticker):
    """
    从 plot_kline.SYMBOL_CONFIG_BY_MARKET 查内置中文名（茅台/腾讯/美光等）。
    懒加载 plot_kline 防止循环依赖。返回空字符串表示无内置。
    """
    try:
        from plot_kline import SYMBOL_CONFIG_BY_MARKET
    except Exception:
        return ''
    cfg = SYMBOL_CONFIG_BY_MARKET.get(market, {}).get(ticker)
    return cfg.get('cn_name', '') if cfg else ''


def _normalize_symbols(raw, market):
    """
    清理 symbols list 为 v4 结构 [{'ticker', 'cn_name'}, ...]。

    支持的输入：
      - v3 旧格式：['BTCUSDT', '600519.SS', ...] 字符串数组
      - v4 新格式：[{'ticker': 'BTCUSDT', 'cn_name': 'BTC'}, ...] dict 数组
      - 混合格式：list 里既有字符串又有 dict（用户手编 json 时可能出现）

    迁移规则：
      - 字符串 ticker → 包成 dict，cn_name 从 plot_kline 内置表查
      - dict 里 cn_name 缺失/空 → 也从内置表查（再 fallback 到空字符串）
      - ticker 全部大写（统一去重，yfinance 大小写不敏感）
      - 按 ticker 去重，保序
    """
    if not isinstance(raw, list):
        return []
    seen = set()
    out = []
    for item in raw:
        ticker = ''
        cn_name = ''
        if isinstance(item, str):
            ticker = item.strip().upper()
        elif isinstance(item, dict):
            ticker = str(item.get('ticker', '')).strip().upper()
            cn_name = str(item.get('cn_name', '')).strip()
        if not ticker or ticker in seen:
            continue
        # cn_name 缺失时从内置表填充。这让"v3 → v4 升级"自动给已知
        # ticker（茅台/腾讯/美光等）补上中文名，无需用户手动操作。
        if not cn_name:
            cn_name = _lookup_builtin_cn_name(market, ticker)
        seen.add(ticker)
        out.append({'ticker': ticker, 'cn_name': cn_name})
    return out


def _build_market_block(raw, market):
    """从磁盘读到的某个 market 子字典构造规范 block。raw 可能是 None。"""
    block = _default_market_block(market)
    if isinstance(raw, dict):
        syms = _normalize_symbols(raw.get('symbols'), market)
        if syms:
            block['symbols'] = syms
        block['ranges'] = _normalize_ranges_for_market(raw.get('ranges'), market)
    return block


def load_settings():
    """
    读取设置。文件缺失或损坏时返回默认值，不抛异常。
    自动处理 v1→v4、v2→v4、v3→v4 迁移(symbols 字符串数组自动包成
    {ticker, cn_name} dict,cn_name 从 plot_kline 内置表查)。
    """
    if not os.path.exists(SETTINGS_FILE):
        return _defaults()
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return _defaults()

    out = _defaults()

    if data.get('language') in ('en', 'zh'):
        out['language'] = data['language']

    # market 字段的迁移：旧版本可能存了 'stock'（v2），现在等价于 'us_stock'
    cur_market = data.get('market')
    if cur_market == 'stock':       # v2 → v3 别名迁移
        cur_market = 'us_stock'
    if cur_market in MARKETS:
        out['market'] = cur_market

    # ── 各 market 块的解析 ──
    has_crypto   = isinstance(data.get('crypto'),   dict)
    has_us_stock = isinstance(data.get('us_stock'), dict)
    has_v2_stock = isinstance(data.get('stock'),    dict)   # v2 的美股块
    has_cn_stock = isinstance(data.get('cn_stock'), dict)
    has_hk_stock = isinstance(data.get('hk_stock'), dict)
    has_v1_top   = ('symbols' in data) or ('ranges' in data)

    # crypto：v3 → v2 → v1 兼容
    if has_crypto:
        out['crypto'] = _build_market_block(data.get('crypto'), 'crypto')
    elif has_v1_top:
        # v1 迁移：旧的顶层 symbols/ranges → crypto
        legacy = {'symbols': data.get('symbols'), 'ranges': data.get('ranges')}
        out['crypto'] = _build_market_block(legacy, 'crypto')
    # else: 用 _defaults() 里的 crypto 默认

    # us_stock：v3 优先；缺失但有 v2 'stock' 块时迁移过来
    if has_us_stock:
        out['us_stock'] = _build_market_block(data.get('us_stock'), 'us_stock')
    elif has_v2_stock:
        # v2 → v3 迁移：'stock' 块原封挪到 'us_stock'
        out['us_stock'] = _build_market_block(data.get('stock'), 'us_stock')
    # else: 用 _defaults() 里的 us_stock 默认

    # cn_stock / hk_stock：v3 才有；缺失就用默认
    if has_cn_stock:
        out['cn_stock'] = _build_market_block(data.get('cn_stock'), 'cn_stock')
    if has_hk_stock:
        out['hk_stock'] = _build_market_block(data.get('hk_stock'), 'hk_stock')

    return out


def save_settings(settings):
    """覆盖写入设置文件。出错时抛异常。

    ensure_ascii=False 让中文 cn_name 在磁盘上以 UTF-8 中文字符存储，
    用户用任何编辑器打开 user_settings.json 都能直接看懂。
    """
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


# ── 便利函数（让调用方少写转换样板）─────────────────────────────────
def extract_tickers(symbol_list):
    """从 v4 symbols 列表抽取纯 ticker 字符串数组。

    用例：main.py / app.py 业务层只需要 ['BTCUSDT', 'ETHUSDT', ...]，
    不需要 cn_name；用这个函数一行搞定。
    """
    return [s.get('ticker', '') if isinstance(s, dict) else str(s)
            for s in (symbol_list or [])]


def extract_name_map(symbol_list):
    """从 v4 symbols 列表抽取 {ticker: cn_name} 字典。

    用例：渲染按钮 / 列表条目时按 ticker 查 cn_name。
    cn_name 为空字符串的 ticker 也会被收录（值是 ''），调用方按需 fallback。
    """
    out = {}
    for s in (symbol_list or []):
        if isinstance(s, dict):
            t = s.get('ticker', '')
            if t:
                out[t] = s.get('cn_name', '') or ''
        elif isinstance(s, str) and s:
            out[s] = ''
    return out


def validate_date_str(s):
    """
    校验 binance 风格日期字符串 '%d %b, %Y'，例 '17 Aug, 2017'。
    成功返回 datetime；失败抛 ValueError。
    """
    return _dt.datetime.strptime(s.strip(), '%d %b, %Y')


# ── 多用户支持：按用户名隔离 settings 文件 ─────────────────────────────
USERS_FILE = os.path.join(_DIR, 'users.json')


def get_user_settings_file(username: str) -> str:
    """返回指定用户的 settings 文件绝对路径。"""
    return os.path.join(_DIR, f'user_settings_{username}.json')


def load_settings_for_user(username: str) -> dict:
    """读取指定用户的 settings，逻辑与 load_settings() 完全一致。"""
    path = get_user_settings_file(username)
    if not os.path.exists(path):
        return _defaults()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return _defaults()

    out = _defaults()
    if data.get('language') in ('en', 'zh'):
        out['language'] = data['language']
    cur_market = data.get('market')
    if cur_market == 'stock':
        cur_market = 'us_stock'
    if cur_market in MARKETS:
        out['market'] = cur_market

    has_crypto   = isinstance(data.get('crypto'),   dict)
    has_us_stock = isinstance(data.get('us_stock'), dict)
    has_v2_stock = isinstance(data.get('stock'),    dict)
    has_cn_stock = isinstance(data.get('cn_stock'), dict)
    has_hk_stock = isinstance(data.get('hk_stock'), dict)
    has_v1_top   = ('symbols' in data) or ('ranges' in data)

    if has_crypto:
        out['crypto'] = _build_market_block(data.get('crypto'), 'crypto')
    elif has_v1_top:
        legacy = {'symbols': data.get('symbols'), 'ranges': data.get('ranges')}
        out['crypto'] = _build_market_block(legacy, 'crypto')

    if has_us_stock:
        out['us_stock'] = _build_market_block(data.get('us_stock'), 'us_stock')
    elif has_v2_stock:
        out['us_stock'] = _build_market_block(data.get('stock'), 'us_stock')

    if has_cn_stock:
        out['cn_stock'] = _build_market_block(data.get('cn_stock'), 'cn_stock')
    if has_hk_stock:
        out['hk_stock'] = _build_market_block(data.get('hk_stock'), 'hk_stock')

    return out


def save_settings_for_user(username: str, data: dict):
    """保存指定用户的 settings。出错时抛异常。"""
    path = get_user_settings_file(username)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── 用户认证 ────────────────────────────────────────────────────────────
import hashlib as _hashlib


def _hash_password(password: str) -> str:
    return 'sha256:' + _hashlib.sha256(password.encode('utf-8')).hexdigest()


def load_users() -> dict:
    """读取 users.json，返回 {username: {password_hash, ...}}。"""
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def verify_password(username: str, password: str) -> bool:
    """验证用户名+密码是否正确。"""
    users = load_users()
    info = users.get(username)
    if not info:
        return False
    stored = info.get('password_hash', '')
    return stored == _hash_password(password)
