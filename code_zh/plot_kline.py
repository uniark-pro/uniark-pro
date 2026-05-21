"""
K线绘图 - 多币种 + 多周期统一入口
==================================
所有"币种"和"周期"相关的差异都收进 SYMBOL_CONFIG 和 INTERVAL_CONFIG，
绘图核心保持单一实现。

币种白名单语义：
  SYMBOL_CONFIG 不再是"允许币种白名单"，只是"显式覆盖配置"。
  settings.py / 设置 UI 加的币种，如果在这里没有显式条目，
  会通过 _resolve_symbol_config() 自动派生（截掉 USDT 后缀作 short）。
  Binance 不存在的交易对会由 Binance API 自己报错。

CLI 输出（供 main.py 解析）：
  - "图片已保存: <path>"   ← 图片路径
  - "BARS=<N>"             ← 实际渲染的 K 线根数

数据拉取策略（精确按需）：
  根据请求的 [start_str, end_str] 计算实际拉取窗口：
      fetch_start = start_str - LOOKBACK_BARS × interval_duration
      fetch_end   = end_str （None 时拉到当下）
  LOOKBACK_BARS=200 让 MA99 在 start_str 处已稳定（99×2+ 缓冲），
  对 MACD 同样足够（5×slow ≈ 130 根即趋同）。

  在主界面 settings 默认时段范围内（weekly 3 年 / 3day 1 年 / daily 4 月），
  实际拉取的总根数恒在 1000 以内，对应一次 Binance HTTP 调用，约 0.3~1 秒。
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


# ── 币种显式配置（可选）──────────────────────────────────────────────
# 只是覆盖默认推导的"显式配置表"。这里没有的币种会走 _resolve_symbol_config
# 自动派生，所以新增币种通常只需要在 settings.py / 设置 UI 里加，无需碰这里。
# 想给某个币种起特殊的 short / 中文名再来这加。
SYMBOL_CONFIG = {
    'BTCUSDT': {'short': 'BTC', 'cn_name': 'BTC'},
    'ETHUSDT': {'short': 'ETH', 'cn_name': 'ETH'},
    'SOLUSDT': {'short': 'SOL', 'cn_name': 'SOL'},
    'BNBUSDT': {'short': 'BNB', 'cn_name': 'BNB'},
    'FILUSDT': {'short': 'FIL', 'cn_name': 'FIL'},
}


# 已知报价币后缀。按长度倒序匹配（USDT 比 USD 优先）。
_QUOTE_SUFFIXES = ('USDT', 'USDC', 'BUSD', 'TUSD', 'FDUSD', 'DAI',
                   'BTC', 'ETH', 'BNB', 'USD', 'EUR')


def _resolve_symbol_config(symbol):
    """
    返回 {'short': ..., 'cn_name': ...}。
    优先 SYMBOL_CONFIG 显式配置；不在表里则从 symbol 自动派生
    （去掉常见报价币后缀作为 short，cn_name 同 short）。
    """
    if symbol in SYMBOL_CONFIG:
        return SYMBOL_CONFIG[symbol]
    short = symbol
    for q in _QUOTE_SUFFIXES:
        if symbol.endswith(q) and len(symbol) > len(q):
            short = symbol[:-len(q)]
            break
    return {'short': short, 'cn_name': short}


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
DATA_FLOOR = pd.Timestamp('2017-07-01')


def calc_ma(series, period):
    return series.rolling(window=period).mean()


def add_ma(df):
    for p in MA_PERIODS:
        df[f'ma{p}'] = calc_ma(df['close'], p)
    return df


def get_macd_colors(hist):
    return ['g' if v >= 0 else 'r' for v in hist]


def _compute_fetch_start(interval, request_start_str):
    """请求起点向前推 LOOKBACK_BARS 根作为实际拉取起点。"""
    request_start = pd.Timestamp(request_start_str)
    minutes = INTERVAL_MINUTES[interval]
    lookback = _dt.timedelta(minutes=minutes * LOOKBACK_BARS)
    fetch_start = request_start - lookback
    if fetch_start < DATA_FLOOR:
        fetch_start = DATA_FLOOR
    return fetch_start.strftime('%d %b, %Y %H:%M:%S')


def render_chart(symbol, interval, start_str=None, end_str=None):
    """渲染指定币种、周期、时间段的 K 线 + MACD + 背离标注图。"""
    if interval not in INTERVAL_CONFIG:
        raise ValueError(
            f"Unknown interval: {interval!r}. "
            f"Valid: {list(INTERVAL_CONFIG.keys())}"
        )

    sym_cfg = _resolve_symbol_config(symbol)
    iv_cfg  = INTERVAL_CONFIG[interval]

    if start_str:
        fetch_start = _compute_fetch_start(interval, start_str)
    else:
        fetch_start = '17 Aug, 2017 00:00:00'

    # 把 end_str 也传给 API，让 Binance 直接截断右端，避免不必要的"拉到现在"。
    # end_str=None 时（用户没指定结束）才让远端拉到当下。
    df = get_klines(symbol, interval,
                    start_str=fetch_start,
                    end_str=end_str if end_str else None)
    df = add_indicators(df)
    df = add_ma(df)
    # 内存过滤保留，处理浮点边界 / 时区折叠等情况（成本忽略不计）
    if start_str:
        df = df[df.index >= pd.Timestamp(start_str)]
    if end_str:
        df = df[df.index <= pd.Timestamp(end_str)]

    if len(df) == 0:
        raise ValueError(
            f"No data in range for {symbol} {interval} "
            f"[{start_str} ~ {end_str}] (fetched from {fetch_start})"
        )

    fmt = '%Y-%m-%d %H:%M' if interval in ('15m', '30m', '1h', '4h') else '%Y-%m-%d'
    start_date = df.index[0].strftime(fmt)
    end_date   = df.index[-1].strftime(fmt)
    title = f"{sym_cfg['short']}USDT {iv_cfg['label']} K-Line\n{start_date} ~ {end_date}"

    macd_colors = get_macd_colors(df['hist'])
    apds = [
        mpf.make_addplot(df[f'ma{MA_PERIODS[0]}'], panel=0, color=MA_COLORS[0], width=1.2, label=f'MA{MA_PERIODS[0]}'),
        mpf.make_addplot(df[f'ma{MA_PERIODS[1]}'], panel=0, color=MA_COLORS[1], width=1.2, label=f'MA{MA_PERIODS[1]}'),
        mpf.make_addplot(df[f'ma{MA_PERIODS[2]}'], panel=0, color=MA_COLORS[2], width=1.5, label=f'MA{MA_PERIODS[2]}'),
        mpf.make_addplot(df['macd'],   panel=2, color='#1f77b4', label='MACD'),
        mpf.make_addplot(df['signal'], panel=2, color='#ff7f0e', label='Signal'),
        mpf.make_addplot(df['hist'],   panel=2, type='bar', color=macd_colors),
    ]

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


def make_output_filename(symbol, interval, df):
    sym_cfg = _resolve_symbol_config(symbol)
    iv_cfg  = INTERVAL_CONFIG[interval]
    fmt = '%Y%m%d_%H%M' if interval in ('15m', '30m', '1h', '4h') else '%Y-%m-%d'
    start_date = df.index[0].strftime(fmt)
    end_date   = df.index[-1].strftime(fmt)
    return f"{sym_cfg['short'].lower()}_{iv_cfg['file_prefix']}_{start_date}_{end_date}.png"


# ── CLI 入口 ────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python plot_kline.py <symbol> <interval> [start_str] [end_str]")
        print(f"  symbol:   any Binance trading pair (e.g. BTCUSDT, DOGEUSDT)")
        print(f"  interval: {list(INTERVAL_CONFIG.keys())}")
        sys.exit(1)

    symbol    = sys.argv[1]
    interval  = sys.argv[2]
    start_str = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
    end_str   = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else None

    fig, df, divs = render_chart(symbol, interval, start_str, end_str)

    sym_cfg = _resolve_symbol_config(symbol)
    iv_cfg  = INTERVAL_CONFIG[interval]

    print(f"时间段内共 {len(df)} 根 {sym_cfg['cn_name']} {iv_cfg['cn_name']}，全部绘图")
    print(f"绘图范围: {df.index[0]} ~ {df.index[-1]}")

    out_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        make_output_filename(symbol, interval, df),
    )
    fig.savefig(out_path, bbox_inches='tight', pad_inches=0.8)
    plt.close(fig)

    print_divergences(df, divs)
    print(f"图片已保存: {out_path}")
    print(f"BARS={len(df)}")
