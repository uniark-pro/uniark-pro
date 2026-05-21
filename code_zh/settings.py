"""
用户设置持久化
================
两个 UI（main.py 桌面端、app.py Web 端）共享同一份 user_settings.json，
存放在脚本所在目录。

字段：
  language : 'en' | 'zh'
  symbols  : list[str]   例 ['BTCUSDT', 'ETHUSDT']
  ranges   : dict        每个 *入口周期* 一份 list[dict(label/start/end)]
                         例 {
                           'weekly': [{label, start, end}, ...],
                           '3day':   [{...}],
                           'daily':  [{...}]
                         }
                         start/end 为 Binance 日期字符串 '%d %b, %Y'
                         （例 '17 Aug, 2017'）

入口周期由 ENTRY_INTERVALS 定义。其余周期（4h/1h/...）只能通过钻取访问，
不在主界面顶级入口出现。

旧格式兼容：v1 时 ranges 是平铺 list；本模块加载时若检测到旧格式
（list 而非 dict），自动把整个 list 视为 weekly 的时段，其余入口用默认值。

文件不存在或解析出错时回退到 DEFAULT_SETTINGS，永不向调用方抛异常。
保存失败（磁盘只读等）会抛异常，由调用方负责提示用户。
"""
import datetime as _dt
import json
import os

_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(_DIR, 'user_settings.json')


# 主界面允许的入口周期。顺序即 UI 上从左到右的展示顺序。
# 添加 / 删除入口时只改这一处 —— UI、settings、迁移逻辑都从它读。
ENTRY_INTERVALS = ('weekly', '3day', 'daily')


# 工厂默认值。改这里只影响 "全新用户 / 删过 user_settings.json 的用户"。
DEFAULT_LANGUAGE = 'en'

DEFAULT_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'FILUSDT']

# 每个入口周期的默认时段。每个时段大约 = 100~150 根 K 线左右的目标长度，
# 跟 navigation.BARS_PER_SEGMENT_TARGET=385 留出余裕（钻一级约切 1~3 段）：
#   weekly  ~3 年   ≈ 156 根
#   3day    ~1 年   ≈ 122 根
#   daily   ~4 月   ≈ 120 根
DEFAULT_RANGES = {
    'weekly': [
        {'label': '2017-08 ~ 2020-05', 'start': '17 Aug, 2017', 'end': '30 May, 2020'},
        {'label': '2020-03 ~ 2022-12', 'start': '17 Mar, 2020', 'end': '30 Dec, 2022'},
        {'label': '2022-10 ~ 2025-10', 'start': '17 Oct, 2022', 'end': '30 Oct, 2025'},
        {'label': '2025-04 ~ 2027-11', 'start': '17 Apr, 2025', 'end': '30 Nov, 2027'},
    ],
    '3day': [
        {'label': '2022-01 ~ 2023-01', 'start': '01 Jan, 2022', 'end': '01 Jan, 2023'},
        {'label': '2023-01 ~ 2024-01', 'start': '01 Jan, 2023', 'end': '01 Jan, 2024'},
        {'label': '2024-01 ~ 2025-01', 'start': '01 Jan, 2024', 'end': '01 Jan, 2025'},
        {'label': '2025-01 ~ 2026-01', 'start': '01 Jan, 2025', 'end': '01 Jan, 2026'},
    ],
    'daily': [
        {'label': '2024-01 ~ 2024-05', 'start': '01 Jan, 2024', 'end': '01 May, 2024'},
        {'label': '2024-05 ~ 2024-09', 'start': '01 May, 2024', 'end': '01 Sep, 2024'},
        {'label': '2024-09 ~ 2025-01', 'start': '01 Sep, 2024', 'end': '01 Jan, 2025'},
        {'label': '2025-01 ~ 2025-05', 'start': '01 Jan, 2025', 'end': '01 May, 2025'},
    ],
}


def _defaults():
    """每次返回独立副本，避免外部 in-place 修改污染默认值。"""
    return {
        'language': DEFAULT_LANGUAGE,
        'symbols':  list(DEFAULT_SYMBOLS),
        'ranges':   {iv: [dict(r) for r in DEFAULT_RANGES.get(iv, [])]
                     for iv in ENTRY_INTERVALS},
    }


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


def _normalize_ranges(raw):
    """
    把磁盘读到的 ranges 字段规整为 {iv: list[range], ...}。

    兼容三种历史形态：
      - dict[interval, list]      新格式
      - list                      旧格式（v1）：当作 weekly 的时段
      - 其他/缺失                 全部走默认值
    """
    out = {iv: [] for iv in ENTRY_INTERVALS}

    if isinstance(raw, list):
        # v1 旧格式：整个列表迁移到 weekly
        out['weekly'] = _clean_range_list(raw)
    elif isinstance(raw, dict):
        for iv in ENTRY_INTERVALS:
            out[iv] = _clean_range_list(raw.get(iv))
    # else: 任何其他类型 → 全部空，下面用默认值兜底

    # 哪个入口空了就回退默认（任何一个入口都不应该是空的，
    # 否则 UI 上会出现"无时间段"提示，但选币种又是默认 5 个，体验割裂）
    for iv in ENTRY_INTERVALS:
        if not out[iv]:
            out[iv] = [dict(r) for r in DEFAULT_RANGES.get(iv, [])]

    return out


def load_settings():
    """读取设置。文件缺失或损坏时返回默认值，不抛异常。"""
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

    syms = data.get('symbols')
    if isinstance(syms, list):
        clean = [str(s).strip().upper()
                 for s in syms
                 if isinstance(s, str) and s.strip()]
        if clean:
            out['symbols'] = clean

    out['ranges'] = _normalize_ranges(data.get('ranges'))

    return out


def save_settings(settings):
    """覆盖写入设置文件。出错时抛异常。"""
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def validate_date_str(s):
    """
    校验 Binance 日期字符串 '%d %b, %Y'，例 '17 Aug, 2017'。
    成功返回 datetime；失败抛 ValueError。
    """
    return _dt.datetime.strptime(s.strip(), '%d %b, %Y')
