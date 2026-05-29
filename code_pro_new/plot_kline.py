"""
K线绘图 - 多 market + 多币种 + 多周期统一入口
================================================
所有"market / 币种 / 周期"相关的差异都收进配置常量和 _resolve_*，
绘图核心保持单一实现。

market 维度
-----------
四个 market：'crypto' | 'us_stock' | 'cn_stock' | 'hk_stock'。
CLI 第一个参数即为 market。
- crypto：binance 数据源，title 含 'USDT' 后缀，DATA_FLOOR=2017-07-01
- us_stock / cn_stock / hk_stock：
    主源 yfinance(auto_adjust 复权) → 备用 akshare(qfq 前复权)，
    路由由 data.py 负责。title 不带 USDT，DATA_FLOOR 按 market 分别设定
    (us_stock/hk_stock = 1970-01-01, cn_stock = 1990-01-01 上交所成立年)。

币种白名单语义：
  SYMBOL_CONFIG_BY_MARKET 不再是"允许币种白名单"，只是"显式覆盖配置"。
  settings.py / 设置 UI 加的 symbol，如果在这里没有显式条目，
  会通过 _resolve_symbol_config() 自动派生（crypto 截掉 USDT 后缀作 short，
  stock 直接用 ticker 本身）。
  数据源不存在的标的会由对应 adapter 自己报错。

CLI 输出（供 main.py 解析）：
  - "图片已保存: <path>"   ← 图片路径
  - "BARS=<N>"             ← 实际渲染的 K 线根数

数据拉取策略（精确按需）：
  根据请求的 [start_str, end_str] 计算实际拉取窗口：
      fetch_start = start_str - LOOKBACK_BARS × interval_duration × trading_factor
      fetch_end   = end_str （None 时拉到当下）
  LOOKBACK_BARS=200 让 MA99 在 start_str 处已稳定（99×2+ 缓冲），
  对 MACD 同样足够（5×slow ≈ 130 根即趋同）。

  关于 trading_factor（交易日因子）：
  ──────────────────────────────────
  crypto 24/7 连续，物理时间 1:1 映射到 K 线根数。但 stock market 的盘中
  粒度（1h / 30m / 15m / 4h）每个交易日只有几个小时有 K 线，单纯按物理时间
  往前推 LOOKBACK_BARS × interval_duration 会严重低估实际需要的日历跨度。

  以 A 股 1h 为例：
      交易时间 09:30-11:30 + 13:00-15:00 = 4h/交易日
      lookback 200 根 1h = 200 个交易小时 = 50 个交易日 ≈ 70 个日历日
      不加 factor 只往前推 200 小时 = 8.3 个日历日 ≈ 24 根 1h K 线
      → MA99 在 start_str 之后还要等 75 根 K 线才有值（约 19 个交易日）

  trading_factor = (24 / 交易小时数) × (7 / 5)
                    │   按日内交易窗口拉伸    │ 按周末补偿日历日
  各 market 1h 粒度因子：
      crypto:    1.0   （24/7 连续，不放大）
      us_stock:  ~5.2  （6.5h/日交易）
      hk_stock:  ~6.1  （5.5h/日交易）
      cn_stock:  ~8.4  （4h/日交易，最严重）

  对 daily / weekly 来说 trading_factor=1（不放大）：yfinance / akshare 只
  返回交易日 bar，节假日不补，按物理时间推 200 天 = ~143 个交易日 ≈ 143 根
  daily（>99，MA99 仍稳定），刚好契合需求。
"""
import sys
import os
import json
import datetime as _dt
import matplotlib
matplotlib.use('Agg')
matplotlib.rcParams['font.sans-serif'] = ['Noto Sans CJK JP', 'WenQuanYi Zen Hei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False

from data import get_klines
from indicator import add_indicators
from divergence import find_three_segment_divergences, find_missed_extremes
from structure_v2 import find_pivots, find_swings
from plot_helpers import annotate_divergences, print_divergences
from navigation import INTERVAL_MINUTES, compute_lookback_factor
from config import (
    MA_PERIODS, MA_COLORS,
    ANCHOR_HIGHLIGHT_COLOR,
    TOO_MUCH_DATA_THRESHOLD,
    LOOKBACK_BARS,
)
import mplfinance as mpf
import matplotlib.pyplot as plt
import pandas as pd


# ── 标的显式配置（按 market 分桶）───────────────────────────────────
# 只是覆盖默认推导的"显式配置表"。这里没有的标的会走 _resolve_symbol_config
# 自动派生，所以新增标的通常只需要在 settings.py / 设置 UI 里加，无需碰这里。
# 想给某个标的起特殊的 short / 中文名再来这加。
#
# short    : 给图表标题、文件名用的 ASCII 短码
# cn_name  : 给中文 UI（按钮、列表条目）用的人类可读名称
#            英文 UI 走 short，所以 short 始终是 ASCII
SYMBOL_CONFIG_BY_MARKET = {
    'crypto': {
        'BTCUSDT': {'short': 'BTC', 'cn_name': 'BTC'},
        'ETHUSDT': {'short': 'ETH', 'cn_name': 'ETH'},
        'SOLUSDT': {'short': 'SOL', 'cn_name': 'SOL'},
        'BNBUSDT': {'short': 'BNB', 'cn_name': 'BNB'},
        'FILUSDT': {'short': 'FIL', 'cn_name': 'FIL'},
    },
    'us_stock': {
        'MU':   {'short': 'MU',   'cn_name': '美光'},
        'NVDA': {'short': 'NVDA', 'cn_name': '英伟达'},
        'TSLA': {'short': 'TSLA', 'cn_name': '特斯拉'},
        'AAPL': {'short': 'GC=F', 'cn_name': '黄金'},
        'MSFT': {'short': 'SI=F', 'cn_name': '白银'},
    },
    'cn_stock': {
        # short 去掉 .SS / .SZ 后缀（图表/文件名更短），cn_name 是中文简称
        '600519.SS': {'short': '600519', 'cn_name': '茅台'},
        '300750.SZ': {'short': '300750', 'cn_name': '宁德时代'},
        '600036.SS': {'short': '600036', 'cn_name': '招商银行'},
        '002594.SZ': {'short': '002594', 'cn_name': '比亚迪'},
        '601318.SS': {'short': '601318', 'cn_name': '中国平安'},
    },
    'hk_stock': {
        # short 去掉 .HK 后缀，cn_name 是港股的中文常用名
        '0700.HK': {'short': '0700', 'cn_name': '腾讯'},
        '9988.HK': {'short': '9988', 'cn_name': '阿里巴巴'},
        '3690.HK': {'short': '3690', 'cn_name': '美团'},
        '1810.HK': {'short': '1810', 'cn_name': '小米'},
        '0388.HK': {'short': '0388', 'cn_name': '港交所'},
    },
}

# 兼容别名，一些旧的 import 路径可能还在引用 SYMBOL_CONFIG。
SYMBOL_CONFIG = SYMBOL_CONFIG_BY_MARKET['crypto']


# 已知报价币后缀（仅 crypto 有）。按长度倒序匹配（USDT 比 USD 优先）。
_QUOTE_SUFFIXES = ('USDT', 'USDC', 'BUSD', 'TUSD', 'FDUSD', 'DAI',
                   'BTC', 'ETH', 'BNB', 'USD', 'EUR')

# A 股 / 港股的交易所后缀。按长度倒序匹配。
_STOCK_SUFFIXES = ('.SS', '.SZ', '.HK')


def _resolve_symbol_config(market, symbol, user_cn_name=None):
    """
    返回 {'short': ..., 'cn_name': ...}。

    cn_name 优先级（高到低）：
      1. user_cn_name 参数（用户在 settings UI 里填的，最高优先级）
      2. SYMBOL_CONFIG_BY_MARKET[market][symbol]['cn_name']（内置表）
      3. fallback：跟 short 同（短码本身做中文 fallback）

    short 总是从 SYMBOL_CONFIG_BY_MARKET 表里取（如有），没有则按 market
    规则自动派生：
      - crypto                       : 去掉常见报价币后缀作为 short
      - cn_stock / hk_stock          : 去掉 .SS / .SZ / .HK 后缀作为 short
      - us_stock 及其他              : ticker 原样作 short

    Parameters
    ----------
    market : str
    symbol : str
    user_cn_name : str | None
        用户在 settings 里给这个标的设置的中文名。空字符串和 None 都视作"未设置"。
    """
    table = SYMBOL_CONFIG_BY_MARKET.get(market, {})
    builtin = table.get(symbol)
    # 算 short
    if builtin:
        short = builtin['short']
    elif market == 'crypto':
        short = symbol
        for q in _QUOTE_SUFFIXES:
            if symbol.endswith(q) and len(symbol) > len(q):
                short = symbol[:-len(q)]
                break
    elif market in ('cn_stock', 'hk_stock'):
        short = symbol
        for s in _STOCK_SUFFIXES:
            if symbol.endswith(s) and len(symbol) > len(s):
                short = symbol[:-len(s)]
                break
    else:
        short = symbol
    # 算 cn_name（按优先级）
    if user_cn_name:
        cn_name = user_cn_name
    elif builtin:
        cn_name = builtin['cn_name']
    else:
        cn_name = short
    return {'short': short, 'cn_name': cn_name}


# ── 周期配置 ──────────────────────────────────────────────────────────
INTERVAL_CONFIG = {
    '15m':    {'label': '15m',    'file_prefix': '15m',    'cn_name': '15分钟', 'min_bars': 0, 'max_level': None},
    '30m':    {'label': '30m',    'file_prefix': '30m',    'cn_name': '30分钟', 'min_bars': 0, 'max_level': None},
    '1h':     {'label': '1h',     'file_prefix': '1h',     'cn_name': '1小时',  'min_bars': 0, 'max_level': None},
    '4h':     {'label': '4h',     'file_prefix': '4h',     'cn_name': '4小时',  'min_bars': 0, 'max_level': None},
    'daily':  {'label': 'Daily',  'file_prefix': 'daily',  'cn_name': '日线',   'min_bars': 0, 'max_level': None},
    '3day':   {'label': '3-Day',  'file_prefix': '3day',   'cn_name': '3日线',  'min_bars': 0, 'max_level': None},
    'weekly': {'label': 'Weekly', 'file_prefix': 'weekly', 'cn_name': '周线',   'min_bars': 0, 'max_level': None},
}



# 数据下界（按 market 分支）。fetch_start 不允许早于该日期。
# crypto: binance 开市于 2017 年；DATA_FLOOR=2017-07-01 留几天缓冲
# 三个 stock market：远早于任何能从 yfinance 拉到的数据起点，等于不设限
DATA_FLOOR_BY_MARKET = {
    'crypto':   pd.Timestamp('2017-07-01'),
    'us_stock': pd.Timestamp('1970-01-01'),
    'cn_stock': pd.Timestamp('1990-01-01'),   # 上交所成立年份，做个象征性下界
    'hk_stock': pd.Timestamp('1970-01-01'),
}


def calc_ma(series, period):
    return series.rolling(window=period).mean()


def add_ma(df):
    for p in MA_PERIODS:
        df[f'ma{p}'] = calc_ma(df['close'], p)
    return df


def get_macd_colors(hist):
    return ['g' if v >= 0 else 'r' for v in hist]


def _compute_fetch_start(market, interval, request_start_str):
    """请求起点向前推 LOOKBACK_BARS 根作为实际拉取起点。

    对 stock 的盘中粒度（1h 等），按"交易日因子"放大物理跨度，让
    实际拿到的额外 K 线数仍 ≈ LOOKBACK_BARS。详见 navigation.compute_lookback_factor。
    """
    request_start = pd.Timestamp(request_start_str)
    minutes = INTERVAL_MINUTES[interval]
    factor = compute_lookback_factor(market, interval)
    lookback = _dt.timedelta(minutes=minutes * LOOKBACK_BARS * factor)
    fetch_start = request_start - lookback
    floor = DATA_FLOOR_BY_MARKET.get(market, pd.Timestamp('1970-01-01'))
    if fetch_start < floor:
        fetch_start = floor
    return fetch_start.strftime('%d %b, %Y %H:%M:%S')


def _make_title(market, sym_cfg, iv_cfg, start_date, end_date, source=None):
    """
    按 market 拼标题。crypto 沿用旧行为带 USDT 后缀；其他三个 stock market
    直接用 ticker 原文（含 .SS / .SZ / .HK 后缀），让用户在标题上一眼看出
    标的所属交易所。

    source 参数：实际使用的数据源（'yfinance' / 'akshare' / 'binance'）。
    主源（crypto=binance / stock=yfinance）走 fallback 之前是默认状态，不挂标记；
    走了 fallback（如 stock 走到 akshare）才在标题尾部加"· 数据源: akshare"，
    让用户看图就知道这次是备份路径，不必去翻终端日志。
    """
    if market == 'crypto':
        head = f"{sym_cfg['short']}USDT {iv_cfg['label']} K-Line"
    else:
        head = f"{sym_cfg['short']} {iv_cfg['label']} K-Line"
    # 走了 fallback 才挂标记。主源不挂保持图表干净。
    # 标记用 ASCII "source" 而非中文"数据源"——matplotlib 默认字体
    # (DejaVu Sans) 不含中文，写中文会渲染成豆腐块（□□□: akshare）。
    # 标题里 ticker / interval / 日期都是 ASCII，统一英文风格也更协调。
    if source and source not in ('binance', 'yfinance'):
        head = f"{head}  ·  source: {source}"
    return f"{head}\n{start_date} ~ {end_date}"


def render_chart(market, symbol, interval, start_str=None, end_str=None,
                 locked_anchor=None):
    """渲染指定 market、币种、周期、时间段的 K 线 + MACD + 背离标注图。

    locked_anchor : dict | None
        锁定锚点的信息包,在主面板上把对应时间段的 MA99 染成红色。结构:
            {
                's3_start_iso': str,   # 大周期 S_last 起始时间(必填)
                's3_end_iso':   str,   # 大周期 S_last 结束时间(必填)
                'peak_iso':     str,   # 大周期极值时间(可选,仅用于显示)
                'kind':         str,   # 'bullish'|'bearish' (可选)
            }

        视觉:主面板 K 线上方的 MA99 折线,在 s3_start ~ s3_end 时间窗内变红——
        MA99 原本浅蓝 (#00aaff),染色子段红色 (#e63946),对比强烈,一眼看出
        "这段时间就是大周期上锁定的极值段"。

        缺 s3 时间窗 / 时间窗超出当前数据范围:静默跳过染色,不阻断绘图。
    """
    if interval not in INTERVAL_CONFIG:
        raise ValueError(
            f"Unknown interval: {interval!r}. "
            f"Valid: {list(INTERVAL_CONFIG.keys())}"
        )

    sym_cfg = _resolve_symbol_config(market, symbol)
    iv_cfg  = INTERVAL_CONFIG[interval]

    if start_str:
        fetch_start = _compute_fetch_start(market, interval, start_str)
    else:
        # 没指定起点：crypto 从 binance 开市拉，stock 拉够画图用即可
        if market == 'crypto':
            fetch_start = '17 Aug, 2017 00:00:00'
        else:
            # 美股：默认拉最近 ~3 年（足够 weekly 一屏 + 充分 lookback）
            three_years_ago = pd.Timestamp.now() - _dt.timedelta(days=365 * 3)
            fetch_start = three_years_ago.strftime('%d %b, %Y %H:%M:%S')

    # 把 end_str 也传给 adapter，让数据源直接截断右端，避免不必要的"拉到现在"。
    # end_str=None 时（用户没指定结束）才让远端拉到当下。
    df = get_klines(market, symbol, interval,
                    start_str=fetch_start,
                    end_str=end_str if end_str else None)
    # 数据源标记早提前抽出来。下游 add_indicators / add_ma / 切片虽然
    # pandas 1.0+ 都会保留 attrs，但稳妥起见在 pipeline 入口就脱钩。
    source = df.attrs.get('source')
    df = add_indicators(df)
    df = add_ma(df)
    # 内存过滤保留，处理浮点边界 / 时区折叠等情况（成本忽略不计）
    # 末尾 .copy()：pandas Copy-on-Write 下布尔切片返回的是只读视图
    # （.values 的 WRITEABLE=False）。下游若有库对数组做原地写入会抛
    # "assignment destination is read-only"。.copy() 强制成可写独立帧。
    if start_str:
        df = df[df.index >= pd.Timestamp(start_str)].copy()
    if end_str:
        df = df[df.index <= pd.Timestamp(end_str)].copy()

    if len(df) == 0:
        raise ValueError(
            f"No data in range for {market} {symbol} {interval} "
            f"[{start_str} ~ {end_str}] (fetched from {fetch_start})"
        )

    fmt = '%Y-%m-%d %H:%M' if interval in ('15m', '30m', '1h', '4h') else '%Y-%m-%d'
    start_date = df.index[0].strftime(fmt)
    end_date   = df.index[-1].strftime(fmt)
    # source 在前面已从 df.attrs 取出。fallback 时图标题挂"· 数据源: akshare"
    title = _make_title(market, sym_cfg, iv_cfg, start_date, end_date, source=source)

    macd_colors = get_macd_colors(df['hist'])

    # 防御性过滤：mplfinance 在 addplot 列全 NaN 时（np.nanmax 空数组）会
    # 抛 "zero-size array to reduction operation maximum which has no identity"。
    # 这会发生在数据不足以支撑长周期 MA / MACD 时，例如：
    #   - 数据少于 99 根 → MA99 全列 NaN
    #   - 数据少于 ~35 根 → MACD/signal/hist 全列 NaN
    # 生产环境下 LOOKBACK_BARS=200 通常足够，但用户配置的极短窗口、
    # 或新上市标的早期历史不足时仍可能触发。这里直接跳过空列。
    def _has_any_valid(col_name):
        return df[col_name].notna().any()

    apds = []
    for i, p in enumerate(MA_PERIODS):
        col = f'ma{p}'
        if _has_any_valid(col):
            apds.append(mpf.make_addplot(
                df[col], panel=0, color=MA_COLORS[i],
                width=1.2 if i < 2 else 1.5, label=f'MA{p}',
            ))
    if _has_any_valid('macd'):
        apds.append(mpf.make_addplot(df['macd'],   panel=2, color='#1f77b4', label='MACD'))
    if _has_any_valid('signal'):
        apds.append(mpf.make_addplot(df['signal'], panel=2, color='#ff7f0e', label='Signal'))
    if _has_any_valid('hist'):
        apds.append(mpf.make_addplot(df['hist'],   panel=2, type='bar', color=macd_colors))

    fig, axes = mpf.plot(
        df,
        type='candle',
        style='charles',
        title=title,
        ylabel='Price',
        volume=True,
        addplot=apds,
        panel_ratios=(4, 1, 2),
        figsize=(14, 10),
        returnfig=True,
        warn_too_much_data=TOO_MUCH_DATA_THRESHOLD,
    )
    fig.subplots_adjust(top=0.93)

    macd_ax = axes[4] if len(axes) >= 5 else None

    divergences = find_three_segment_divergences(
        df['hist'], df['low'], df['high'],
        min_bars=iv_cfg['min_bars'],
        max_level=iv_cfg['max_level'],
    )
    # 补检「价格极值落在反向 hist 段」的背离（标准三段背离漏掉、由红/绿
    # 反向段承载的顶/底极值）。并入同一列表 —— annotate / serialize / 钻取
    # 全程沿用既有管线，标准检测逻辑不受影响。不与标准背离交叉去重：两者
    # 锚点必在不同颜色的段、不同 K 线上，结构上不可能重复。
    divergences += find_missed_extremes(
        df['hist'], df['low'], df['high'],
    )
    divergences.sort(key=lambda d: (d['s3_start'], d['level']))
    if macd_ax is not None:
        annotate_divergences(macd_ax, df, divergences)

    # ── 锁定锚点:主面板 MA99 染色 + 背景投影 ──────────────────────────
    # 把 locked_anchor 里的 s3_start_iso ~ s3_end_iso 时间窗投影到当前周期 df
    # 上,得到 [lo, hi] 闭区间下标。然后:
    #   (1) 主面板叠加红色 MA99 子线段,覆盖该区间(原有功能,保留)
    #   (2) 三个面板背景同时叠加半透明色带(本次新增)
    #   (3) 主面板叠加两条水平虚线,标出上级别 S3 段的价格极值(本次新增)
    #
    # 投影规则（关键细节：父级 K 线 open_time 的右边界）:
    #   s3_start_iso / s3_end_iso 都是父级 K 线的 open_time。一根 K 线"代表"
    #   的物理时间窗是 [open_time, open_time + 一根 K 线时长)。所以:
    #     - lo 用 s3_start 直接 searchsorted left  ——父级首根 K 线的起点
    #     - hi 用 (s3_end + 一根父级 K 线时长) searchsorted left - 1
    #       ——父级末根 K 线在物理时间上覆盖的最右子 K 线
    #
    #   反例:如果 hi 直接用 s3_end searchsorted right - 1,父级末根 K 线在
    #   子级别上只染了"第一根",举例 weekly 末段在 daily 上染色会少 6 天。
    #
    #   父级周期由 locked_anchor['parent_interval'] 提供(必备字段)。缺字段或
    #   未知周期时退回旧 right-edge 行为,保证向后兼容。
    # 找不到对应区间/区间空/缺 s3 字段 → 静默跳过,锚点是锦上添花。
    if locked_anchor and len(axes) >= 1 and _has_any_valid('ma99'):
        try:
            anchor_dict = (locked_anchor if isinstance(locked_anchor, dict)
                           else {})
            s3s_iso = anchor_dict.get('s3_start_iso')
            s3e_iso = anchor_dict.get('s3_end_iso')
            if s3s_iso and s3e_iso:
                s3s_ts = pd.Timestamp(s3s_iso)
                s3e_ts = pd.Timestamp(s3e_iso)
                if s3s_ts.tzinfo is not None:
                    s3s_ts = s3s_ts.tz_localize(None)
                if s3e_ts.tzinfo is not None:
                    s3e_ts = s3e_ts.tz_localize(None)
                # 父级周期 → 一根父 K 线的物理时间。缺字段或未知时退回旧行为。
                parent_iv = anchor_dict.get('parent_interval')
                parent_bar_min = INTERVAL_MINUTES.get(parent_iv)
                lo = int(df.index.searchsorted(s3s_ts, side='left'))
                if parent_bar_min:
                    s3e_right_edge = s3e_ts + _dt.timedelta(minutes=parent_bar_min)
                    hi = int(df.index.searchsorted(s3e_right_edge, side='left')) - 1
                else:
                    hi = int(df.index.searchsorted(s3e_ts, side='right')) - 1
                # 区间至少 2 根才有意义、要落在当前数据范围内
                if 0 <= lo < hi < len(df):

                    # ── (2) 背景色带:三个面板同步叠加半透明色块 ───────
                    # 颜色按背离方向区分,沿用项目内 bullish/bearish 配色:
                    #   bullish (底背离 → 多头偏向) = #ff5566 暖红
                    #   bearish (顶背离 → 空头偏向) = #22cc44 冷绿
                    # alpha 极低(0.06) 避免与 K 线本体的鲜红/亮绿混淆,
                    # 仅作为"背景上下文"提示而非视觉焦点。
                    kind = anchor_dict.get('kind', 'bullish')
                    band_color = '#ff5566' if kind == 'bullish' else '#22cc44'
                    # axvspan 用 ±0.5 让色带正好对齐 K 线柱的左右边界
                    x_left  = lo - 0.5
                    x_right = hi + 0.5
                    for ax_idx in (0, 2, 4):    # 价格 / 量 / MACD
                        if ax_idx < len(axes):
                            axes[ax_idx].axvspan(
                                x_left, x_right,
                                color=band_color,
                                alpha=0.07,
                                zorder=0,        # 压在所有元素最底层
                            )

                    # ── (3) 价格水平虚线:S3 段价格极值,仅主面板 ──────
                    # 用 df 在 [lo, hi] 区间的 low.min() / high.max() 作为
                    # 上级别 S3 段的真实价格极值。这两条线告诉使用者:
                    # "上级别关心的价格区间在 [s3_low, s3_high]"
                    s3_window = df.iloc[lo:hi + 1]
                    s3_low_price  = float(s3_window['low'].min())
                    s3_high_price = float(s3_window['high'].max())
                    price_ax = axes[0]
                    for price, label_prefix in (
                        (s3_high_price, 'S3 高'),
                        (s3_low_price,  'S3 低'),
                    ):
                        price_ax.axhline(
                            y=price,
                            color=ANCHOR_HIGHLIGHT_COLOR,
                            linewidth=1.2,
                            linestyle='--',
                            alpha=0.75,
                            zorder=2,        # 在 K 线之下、网格之上
                        )
                        # 右端价格标签:浮在图内右侧
                        price_ax.text(
                            len(df) - 1, price,
                            f' {label_prefix} {price:,.2f}',
                            color=ANCHOR_HIGHLIGHT_COLOR,
                            fontsize=9,
                            fontweight='bold',
                            va='center',
                            ha='left',
                            zorder=11,
                            bbox=dict(
                                boxstyle='round,pad=0.25',
                                facecolor='white',
                                edgecolor=ANCHOR_HIGHLIGHT_COLOR,
                                linewidth=0.6,
                                alpha=0.85,
                            ),
                        )

                    # ── (1) MA99 子段染色 ─────────────────────────────
                    xs = list(range(lo, hi + 1))
                    ys = df['ma99'].iloc[lo:hi + 1].values
                    price_ax.plot(
                        xs, ys,
                        color=ANCHOR_HIGHLIGHT_COLOR,
                        linewidth=2.2,
                        zorder=10,                # 盖在原浅蓝 MA99 (zorder ~2) 上方
                        solid_capstyle='round',
                    )
        except Exception:
            # 解析失败 / 序列空 / 任何异常都静默
            pass

    # ── 走势段连线 + 转折点编号 ───────────────────────────────────────────
    try:
        swings = find_swings(df['hist'], df['low'], df['high'])
        pivots = find_pivots(df['hist'], df['low'], df['high'])
        price_ax = axes[0]
        for sn, swing in enumerate(swings):
            x0, y0 = swing.start_pivot.bar_idx, swing.start_pivot.price
            x1, y1 = swing.end_pivot.bar_idx,   swing.end_pivot.price
            color = '#00cc88' if swing.direction == 'up' else '#ff4455'
            price_ax.plot([x0, x1], [y0, y1],
                          color=color, linewidth=1.2, alpha=0.75, zorder=9)
            # S0, S1, S2... 标注在连线中点
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            price_ax.text(mx, my, f'S{sn}',
                          color=color, fontsize=7, fontweight='bold',
                          ha='center', va='center', zorder=11,
                          bbox=dict(boxstyle='round,pad=0.15', facecolor='#1e1e2e',
                                    edgecolor=color, linewidth=0.6, alpha=0.8))
        # ── 盘整区间辅助函数 ─────────────────────────────────────────────────
        import matplotlib.patches as _patches
        def swing_high(sw):
            return max(sw.start_pivot.price, sw.end_pivot.price)
        def swing_low(sw):
            return min(sw.start_pivot.price, sw.end_pivot.price)
        def draw_consolidation(ax, sw_a, sw_b, sw_c, x_end, label, color):
            hi = min(swing_high(sw_a), swing_high(sw_b), swing_high(sw_c))
            lo = max(swing_low(sw_a),  swing_low(sw_b),  swing_low(sw_c))
            if hi <= lo:
                return
            x0 = sw_a.start_pivot.bar_idx
            rect = _patches.Rectangle(
                (x0, lo), x_end - x0, hi - lo,
                linewidth=1.2, edgecolor=color,
                facecolor=color + '15', zorder=8,
            )
            ax.add_patch(rect)
            # 标签在方框右侧中间：P名 在上，价格区间 在下
            mid_y = (hi + lo) / 2
            ax.text(x_end + 0.8, mid_y + (hi - lo) * 0.12, label,
                    color=color, fontsize=8, fontweight='bold',
                    ha='left', va='center', zorder=12)
            ax.text(x_end + 0.8, mid_y - (hi - lo) * 0.12,
                    f'{lo:,.0f}-{hi:,.0f}',
                    color=color, fontsize=7,
                    ha='left', va='center', zorder=12)

        # ── 通用盘整区间扫描：P1, P2, P3... ────────────────────────────────
        # 颜色轮换
        CON_COLORS = ['#ffaa00', '#aa88ff', '#00ccff', '#ff88aa', '#88ffaa']
        con_num = 0          # 当前盘整编号（0-based，显示时+1）
        prev_hi = None       # 上一个盘整区间上界
        prev_lo = None       # 上一个盘整区间下界
        scan_from = 1        # 从哪个段开始扫描

        while scan_from <= len(swings) - 3:
            # 1. 从 scan_from 开始滑动找第一个有重叠区间的三段
            found_i = None
            for i in range(scan_from, len(swings) - 2):
                hi = min(swing_high(swings[i]), swing_high(swings[i+1]), swing_high(swings[i+2]))
                lo = max(swing_low(swings[i]),  swing_low(swings[i+1]),  swing_low(swings[i+2]))
                if hi > lo:
                    # 2. 检查与上一个盘整区间无交集
                    if prev_hi is None or lo > prev_hi or hi < prev_lo:
                        found_i = i
                        break

            if found_i is None:
                break

            i = found_i
            hi = min(swing_high(swings[i]), swing_high(swings[i+1]), swing_high(swings[i+2]))
            lo = max(swing_low(swings[i]),  swing_low(swings[i+1]),  swing_low(swings[i+2]))

            # 3. 向右延伸：后续段价格回到当前盘整区间内则延伸
            # 不用 break：遇到离开区间的段继续往右找，直到找不到回来的段为止
            x_end = swings[i+2].end_pivot.bar_idx
            last_in_j = i + 2
            for j in range(i+3, len(swings)):
                sh = swing_high(swings[j])
                sl = swing_low(swings[j])
                if sl < hi and sh > lo:   # 回到区间内，延伸
                    x_end = swings[j].end_pivot.bar_idx
                    last_in_j = j

            # 4. 画出方框
            label = 'P' + str(con_num + 1)
            color = CON_COLORS[con_num % len(CON_COLORS)]
            draw_consolidation(price_ax,
                               swings[i], swings[i+1], swings[i+2],
                               x_end=x_end, label=label, color=color)

            # 5. 更新状态，从离开该盘整的下一段继续扫描
            prev_hi = hi
            prev_lo = lo
            con_num += 1
            scan_from = last_in_j + 1

        for num, p in enumerate(pivots, start=1):
            color = '#00cc88' if p.kind == 'low' else '#ff4455'
            price_ax.scatter(p.bar_idx, p.price,
                             color=color, s=40, zorder=10,
                             marker='o', edgecolors='white', linewidths=0.5)
            # 数字偏移：低点往下 3%，高点往上 3%，与圆点保持距离
            y_offset = -p.price * 0.03 if p.kind == 'low' else p.price * 0.03
            price_ax.text(p.bar_idx, p.price + y_offset, str(num),
                          color=color, fontsize=8, fontweight='bold',
                          ha='center', va='top' if p.kind == 'low' else 'bottom',
                          zorder=11)
    except Exception:
        pass

    return fig, df, divergences


def make_output_filename(market, symbol, interval, df):
    sym_cfg = _resolve_symbol_config(market, symbol)
    iv_cfg  = INTERVAL_CONFIG[interval]
    fmt = '%Y%m%d_%H%M' if interval in ('15m', '30m', '1h', '4h') else '%Y-%m-%d'
    start_date = df.index[0].strftime(fmt)
    end_date   = df.index[-1].strftime(fmt)
    # ticker 里可能含 '.'（A 股 600519.SS / 港股 0700.HK），文件名里换成 '_'
    # 避免 Windows 把 '.SS' 当作扩展名误判
    short_safe = sym_cfg['short'].lower().replace('.', '_')
    return f"{market}_{short_safe}_{iv_cfg['file_prefix']}_{start_date}_{end_date}.png"


def serialize_divergences(df, divs):
    """
    把 find_three_segment_divergences 的输出转成"UI 可直接消费"的精简
    dict 列表,只保留下游(main.py / app.py)真正用到的字段,并附上极值
    时间点 peak_iso——背离钻取的核心锚点——以及 s3 时间窗的两端 ISO 时间
    (next-level 钻取时,plot_kline 用它来锚定主面板上的 MA99 染色范围)。

    极值定义:
      bullish → S_last 区间内 low 最小的那根 K 线的 open_time
      bearish → S_last 区间内 high 最大的那根 K 线的 open_time

    极值是"S_last 内部"的极值,不是整个 P 跨度的极值。背离判定要求
    S_last 创出比左侧主体更深的新低/更高的新高,所以 S_last 内极值
    必然是当前可见跨度上的真正极值。

    NaN 处理:
      用 np.nanargmin / np.nanargmax 而不是 ndarray.argmin。
      普通 argmin 在含 NaN 数组上会把 NaN 当作 < 任何值返回 NaN 位置,
      这是潜在 bug——stock 数据偶尔停盘/拆分会留下 NaN low/high。
      全 NaN 段时 nanargmin 抛 ValueError,该 div 整条跳过(罕见但兜底)。

    输出按 s3_end 升序(等同于按时间顺序),恰好就是
    find_three_segment_divergences 返回时的排序——故此处不再重排。
    """
    import numpy as np
    out = []
    for d in divs:
        s3s, s3e = d['s3_start'], d['s3_end']
        seg_low  = df['low'].iloc[s3s:s3e + 1]
        seg_high = df['high'].iloc[s3s:s3e + 1]
        try:
            if d['kind'] == 'bullish':
                peak_pos = int(np.nanargmin(seg_low.values))
                peak_idx = s3s + peak_pos
                peak_price = float(seg_low.iloc[peak_pos])
            else:
                peak_pos = int(np.nanargmax(seg_high.values))
                peak_idx = s3s + peak_pos
                peak_price = float(seg_high.iloc[peak_pos])
        except ValueError:
            # S_last 段全 NaN —— 极罕见,该 div 整条跳过(下游不会拿到错误锚点)
            continue
        peak_ts = df.index[peak_idx]
        out.append({
            'kind':        d['kind'],
            'level':       int(d['level']),
            'ratio':       float(d['ratio']),
            'provisional': bool(d.get('provisional', False)),
            'same_terminal_l1': bool(d.get('same_terminal_l1', False)),
            # 极值时间(底背离=最低价时间,顶背离=最高价时间)和价格
            'peak_iso':    peak_ts.isoformat(),
            'peak_price':  peak_price,
            # S_last 时间窗两端(用于子周期钻取时在 MA99 上染色)
            's3_start_iso': df.index[s3s].isoformat(),
            's3_end_iso':   df.index[s3e].isoformat(),
        })
    return out


# ── CLI 入口 ────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python plot_kline.py <market> <symbol> <interval> [start_str] [end_str] [locked_anchor_json]")
        print("  market:   'crypto' | 'us_stock' | 'cn_stock' | 'hk_stock'")
        print("  symbol:   crypto 标的(例 BTCUSDT, DOGEUSDT)或 stock 标的(例 MU, NVDA)")
        print(f"  interval: {list(INTERVAL_CONFIG.keys())}")
        print("  locked_anchor_json: 锁定锚点信息包的 JSON 字符串(可选),例如:")
        print('    \'{"s3_start_iso":"2020-03-10","s3_end_iso":"2020-03-16","peak_iso":"2020-03-12","kind":"bullish"}\'')
        sys.exit(1)

    market    = sys.argv[1]
    symbol    = sys.argv[2]
    interval  = sys.argv[3]
    start_str = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else None
    end_str   = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] else None

    # 锚点参数:JSON 字符串解析为 dict;空串/解析失败都视为无锚点
    locked_anchor = None
    if len(sys.argv) > 6 and sys.argv[6]:
        try:
            parsed = json.loads(sys.argv[6])
            if isinstance(parsed, dict):
                locked_anchor = parsed
        except (json.JSONDecodeError, ValueError):
            pass

    fig, df, divs = render_chart(market, symbol, interval, start_str, end_str,
                                 locked_anchor=locked_anchor)

    sym_cfg = _resolve_symbol_config(market, symbol)
    iv_cfg  = INTERVAL_CONFIG[interval]

    print(f"时间段内共 {len(df)} 根 {sym_cfg['cn_name']} {iv_cfg['cn_name']}，全部绘图")
    print(f"绘图范围: {df.index[0]} ~ {df.index[-1]}")

    out_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        make_output_filename(market, symbol, interval, df),
    )
    fig.savefig(out_path, bbox_inches='tight', pad_inches=0.8)
    plt.close(fig)

    print_divergences(df, divs)
    print(f"图片已保存: {out_path}")
    print(f"BARS={len(df)}")
    # 把背离信号以 JSON 单行形式输出给上层 UI——main.py 用 subprocess 调用
    # 时通过正则 ^DIVS_JSON=... 解析。一行限定让正则能稳定捕获。
    print(f"DIVS_JSON={json.dumps(serialize_divergences(df, divs), ensure_ascii=False)}")
