"""
User settings persistence
========================
The two UIs (main.py desktop, app.py web) share the same
user_settings.json, located in the script's directory.

Fields:
  language : 'en' | 'zh'
  symbols  : list[str]   e.g. ['BTCUSDT', 'ETHUSDT']
  ranges   : dict        one entry per *entry interval*:
                         each is list[dict(label/start/end)]
                         e.g. {
                           'weekly': [{label, start, end}, ...],
                           '3day':   [{...}],
                           'daily':  [{...}]
                         }
                         start/end are Binance date strings '%d %b, %Y'
                         (e.g. '17 Aug, 2017')

The set of entry intervals is defined by ENTRY_INTERVALS. Other
intervals (4h / 1h / ...) are reachable only by drilling down; they
do not appear at the top level of the main UI.

Legacy format compat: in v1, `ranges` was a flat list. When loaded,
the module detects the legacy format (a list rather than a dict) and
treats the entire list as the weekly ranges; other entry intervals
fall back to defaults.

When the file is missing or unparseable, fall back to
DEFAULT_SETTINGS — never raise to the caller. Save errors (read-only
disk, etc.) do raise; the caller is expected to surface those to
the user.
"""
import datetime as _dt
import json
import os

_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(_DIR, 'user_settings.json')


# Entry intervals allowed in the main UI. The order here is the
# left-to-right display order. Add or remove entries here only —
# the UI, settings, and migration logic all read from this constant.
ENTRY_INTERVALS = ('weekly', '3day', 'daily')


# Factory defaults. Changing these only affects "brand-new users /
# users who deleted user_settings.json".
DEFAULT_LANGUAGE = 'en'

DEFAULT_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'FILUSDT']

# Default time range for each entry interval. Each range targets ~100
# to 150 K-lines, leaving headroom for navigation.BARS_PER_SEGMENT_TARGET
# = 385 (drilling down by one level produces 1-3 sub-segments):
#   weekly  ~3 years   ≈ 156 bars
#   3day    ~1 year    ≈ 122 bars
#   daily   ~4 months  ≈ 120 bars
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
    """Return an independent copy each time, so external in-place
    edits don't pollute the defaults."""
    return {
        'language': DEFAULT_LANGUAGE,
        'symbols':  list(DEFAULT_SYMBOLS),
        'ranges':   {iv: [dict(r) for r in DEFAULT_RANGES.get(iv, [])]
                     for iv in ENTRY_INTERVALS},
    }


def _clean_range_list(rngs):
    """Filter a list, keeping only dicts that have all three of
    label/start/end."""
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
    Normalize the on-disk `ranges` field into {iv: list[range], ...}.

    Compatible with three historical shapes:
      - dict[interval, list]      new format
      - list                      legacy format (v1): treated as weekly
      - other / missing           fall through to defaults entirely
    """
    out = {iv: [] for iv in ENTRY_INTERVALS}

    if isinstance(raw, list):
        # v1 legacy format: migrate the whole list to weekly
        out['weekly'] = _clean_range_list(raw)
    elif isinstance(raw, dict):
        for iv in ENTRY_INTERVALS:
            out[iv] = _clean_range_list(raw.get(iv))
    # else: any other type → all empty; defaults below fill in

    # Whichever entry is empty, fall back to defaults (no entry should
    # ever be empty — otherwise the UI shows a "no time ranges"
    # prompt while symbols still default to 5, which feels broken).
    for iv in ENTRY_INTERVALS:
        if not out[iv]:
            out[iv] = [dict(r) for r in DEFAULT_RANGES.get(iv, [])]

    return out


def load_settings():
    """Load settings. If the file is missing or corrupt, return defaults
    — never raise."""
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
    """Overwrite the settings file. Raises on error."""
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def validate_date_str(s):
    """
    Validate a Binance date string '%d %b, %Y', e.g. '17 Aug, 2017'.
    Returns datetime on success; raises ValueError on failure.
    """
    return _dt.datetime.strptime(s.strip(), '%d %b, %Y')
