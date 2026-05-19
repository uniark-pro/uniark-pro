"""
数据路由层 - 按 market 分发到对应 adapter（懒加载 + fallback 链）
======================================================================

接口约定
--------
对外只暴露一个函数 get_klines(market, symbol, interval, start_str, end_str)：
    - market   : 'crypto' | 'us_stock' | 'cn_stock' | 'hk_stock'
    - symbol   : 'BTCUSDT' / 'MU' / '600519.SS' / '0700.HK' 等
    - interval : 'weekly' | 'daily' | '1h' | ...（具体支持哪些由对应 adapter 决定）
    - start_str: '17 Aug, 2017' 格式的日期字符串
    - end_str  : 同上，None 表示拉到当下

返回 DataFrame：index=tz-naive DatetimeIndex，
columns=['open','high','low','close','volume']，全 float64。
DataFrame.attrs['source'] 记录实际使用的数据源（'yfinance' / 'akshare' /
'binance'），上层 plot_kline 据此在标题上挂"数据源"标记。

数据源策略
----------
crypto:
    binance（无 fallback，币圈只有这个数据源）

stock (us_stock / cn_stock / hk_stock):
    主：yfinance
    备：akshare
    触发条件：yfinance 返回 < MIN_BARS_FOR_PRIMARY 行 → 自动 fallback 到 akshare

为什么需要 fallback：
yfinance 1.3.0 在 2025-2026 年持续退化（Yahoo 端反爬升级、新上市股拉不到
历史等）。具体复现：0100.HK MiniMax 用 yfinance 只返 1 行，akshare 拉
全历史无问题。akshare 走中文数据源（新浪/东财/腾讯），跟 Yahoo 完全独立。

懒加载
------
adapter 只在 first call 时 import 对应模块。第三方包缺失时抛出明确
ImportError，提示用户该装哪个包。
"""
import importlib
import sys
import pandas as pd

# market → adapter 链（按尝试顺序）
# 三个 stock market 都用 yfinance → akshare 串联
_ROUTERS = {
    'crypto':   ['data_binance'],
    'us_stock': ['data_yfinance', 'data_akshare'],
    'cn_stock': ['data_yfinance', 'data_akshare'],
    'hk_stock': ['data_yfinance', 'data_akshare'],
}

# 数据源短名映射（用于 df.attrs['source']）
_SOURCE_NAME = {
    'data_binance':  'binance',
    'data_yfinance': 'yfinance',
    'data_akshare':  'akshare',
}

# fallback 阈值：主 adapter 返回少于这个行数就走备用
# 30 根：MA99/MACD 都算不准的红线，明显是数据获取失败
MIN_BARS_FOR_PRIMARY = 30

# 已加载的子模块缓存：{module_name: module}
_LOADED = {}


def _get_adapter(module_name):
    """按需 import 并缓存 adapter。"""
    if module_name in _LOADED:
        return _LOADED[module_name]
    try:
        mod = importlib.import_module(module_name)
    except ImportError as e:
        raise ImportError(
            f"加载数据 adapter ({module_name}) 失败：{e}"
        ) from e
    _LOADED[module_name] = mod
    return mod


def get_klines(market, symbol, interval, start_str, end_str=None):
    """
    按 market 路由 + fallback 链调用 adapter。

    返回 DataFrame，attrs['source'] 标注实际使用的数据源。
    """
    if market not in _ROUTERS:
        raise ValueError(
            f"未知 market: {market!r}。已注册: {list(_ROUTERS.keys())}"
        )

    chain = _ROUTERS[market]
    last_error = None
    last_df = None
    last_source = None

    for idx, module_name in enumerate(chain):
        is_primary = (idx == 0)
        is_last = (idx == len(chain) - 1)
        source = _SOURCE_NAME.get(module_name, module_name)

        try:
            adapter = _get_adapter(module_name)
            df = adapter.get_klines(symbol, interval, start_str, end_str)
        except Exception as e:
            # adapter 抛异常：记录后继续尝试下一个
            print(f"[ROUTE] {market}/{symbol} {interval}: {source} 抛异常 "
                  f"({type(e).__name__}: {str(e)[:80]})", file=sys.stderr)
            last_error = e
            if is_last:
                # 整个链都失败，重抛最后一个异常
                raise
            continue

        n = len(df) if df is not None else 0
        last_df = df
        last_source = source
        print(f"[ROUTE] {market}/{symbol} {interval}: {source} 返回 {n} 行",
              file=sys.stderr)

        # 主 adapter 数据足够 → 直接采用，不走 fallback
        if is_primary and n >= MIN_BARS_FOR_PRIMARY:
            df.attrs['source'] = source
            return df

        # 备用 adapter 数据足够 → 采用（哪怕没满 30 根，备用就到此为止）
        if not is_primary and n > 0:
            df.attrs['source'] = source
            print(f"[ROUTE] {market}/{symbol} {interval}: 采用 fallback 数据源 "
                  f"{source}（主源数据不足）", file=sys.stderr)
            return df

        # 主源数据不足且还有备用 → 继续 for 循环
        if is_primary and not is_last:
            print(f"[ROUTE] {market}/{symbol} {interval}: 主源 {source} "
                  f"返回 {n} 行 < {MIN_BARS_FOR_PRIMARY}，尝试 fallback",
                  file=sys.stderr)
            continue

        # 走到这里说明：备用也不行，或主源不行且没有备用
        if is_last:
            # 把最后拿到的（可能是空/不足的）DataFrame 退还给上层，
            # 让 plot_kline 走"无足够数据"的错误路径而不是这里乱抛
            if df is not None:
                df.attrs['source'] = source
                return df
            raise RuntimeError(
                f"所有数据源都返回空：{market}/{symbol} {interval}"
            )

    # 理论上到不了这里
    if last_df is not None:
        last_df.attrs['source'] = last_source
        return last_df
    raise RuntimeError(f"未找到可用数据源：{market}/{symbol} {interval}")
