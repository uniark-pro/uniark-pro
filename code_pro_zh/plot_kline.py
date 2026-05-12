"""
K线绘图 - 多 market + 多币种 + 多周期统一入口
================================================
所有"market / 币种 / 周期"相关的差异都收进配置常量和 _resolve_*，
绘图核心保持单一实现。

market 维度
-----------
新增 'crypto' | 'stock' 维度。CLI 第一个参数即为 market。
- crypto：沿用 binance 数据源，title 含 'USDT' 后缀，DATA_FLOOR=2017-07-01
- stock ：走 yfinance 数据源（auto_adjust 复权），title 不带 USDT，
          DATA_FLOOR 放在 1970-01-01（远早于任何能拿到的美股历史）

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
      fetch_start = start_str - LOOKBACK_BARS × interval_duration
      fetch_end   = end_str （None 时拉到当下）
  LOOKBACK_BARS=200 让 MA99 在 start_str 处已稳定（99×2+ 缓冲），
  对 MACD 同样足够（5×slow ≈ 130 根即趋同）。

  注意：crypto 周期是物理时间（1 周 = 7×24h），美股周期是交易日时间
  （1 周 = 5 个交易日，跨周末有断点）。LOOKBACK_BARS 按物理时间往前推
  在美股下会"多拉一些日历日，得到的实际 K 线根数仍约等于 200"——
  yfinance 自己只返回交易日 bar，节假日不补，刚好契合需求。
"""
import sys
import os
import datetime as _dt
import matplotlib
matplotlib.use('Agg')
matplotlib.rcParams['font.sans-serif'] = ['Noto Sans CJK JP', 'WenQuanYi Zen Hei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False

from data import get_klines
from indicator import add_indicators
from divergence import find_three_segment_divergences
from plot_helpers import annotate_divergences, print_divergences
from navigation import INTERVAL_MINUTES
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
        'AAPL': {'short': 'AAPL', 'cn_name': '苹果'},
        'TSLA': {'short': 'TSLA', 'cn_name': '特斯拉'},
        'MSFT': {'short': 'MSFT', 'cn_name': '微软'},
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


MA_PERIODS = (7, 25, 99)
MA_COLORS  = ('#ff9900', '#cc44ff', '#00aaff')

TOO_MUCH_DATA_THRESHOLD = 600
LOOKBACK_BARS = 200

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
    """请求起点向前推 LOOKBACK_BARS 根作为实际拉取起点。"""
    request_start = pd.Timestamp(request_start_str)
    minutes = INTERVAL_MINUTES[interval]
    lookback = _dt.timedelta(minutes=minutes * LOOKBACK_BARS)
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


def render_chart(market, symbol, interval, start_str=None, end_str=None):
    """渲染指定 market、币种、周期、时间段的 K 线 + MACD + 背离标注图。"""
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
    if start_str:
        df = df[df.index >= pd.Timestamp(start_str)]
    if end_str:
        df = df[df.index <= pd.Timestamp(end_str)]

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
    if macd_ax is not None:
        annotate_divergences(macd_ax, df, divergences)

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


# ── CLI 入口 ────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python plot_kline.py <market> <symbol> <interval> [start_str] [end_str]")
        print("  market:   'crypto' | 'us_stock' | 'cn_stock' | 'hk_stock'")
        print("  symbol:   crypto 标的（例 BTCUSDT, DOGEUSDT）或 stock 标的（例 MU, NVDA）")
        print(f"  interval: {list(INTERVAL_CONFIG.keys())}")
        sys.exit(1)

    market    = sys.argv[1]
    symbol    = sys.argv[2]
    interval  = sys.argv[3]
    start_str = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else None
    end_str   = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] else None

    fig, df, divs = render_chart(market, symbol, interval, start_str, end_str)

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
