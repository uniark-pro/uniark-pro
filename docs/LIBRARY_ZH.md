# divergence.py

> 基于三段结构的 MACD 背离检测器，支持分层扩展和反向屏障过滤。

一个单文件 Python 库，用结构化、公理化的方法检测 MACD 柱状图上的
**底背离和顶背离**：

- **三段结构（S1 + S2 + S3）** —— 按 hist 正负号切段，再按"同向 / 反向 / 同向"三段窗口扫描。
- **分层扩展** —— 递归构造 `P + S(2k) + S(2k+1)`，识别趋势级别的背离（Lv2、Lv3、……）。
- **反向屏障过滤** —— 自动剔除内部被反向 Lv≥2 背离打断的结构（结构两端分属不同机制，不应合并）。
- **暂定信号标记** —— 末段还没"封口"的信号会被标记出来，避免在回测中引入 lookahead bias。

---

## 安装

单文件 + 一个依赖（`numpy`）。把 `divergence.py` 丢进项目，或者：

```bash
# 依赖
pip install numpy pandas
```

---

## 快速开始

```python
import pandas as pd
from divergence import find_three_segment_divergences

# 1) 准备 OHLCV 数据
df = pd.read_csv('btc_daily.csv', parse_dates=['date'], index_col='date')

# 2) 算 MACD（12/26/9）
ema_fast = df['close'].ewm(span=12, adjust=False).mean()
ema_slow = df['close'].ewm(span=26, adjust=False).mean()
df['hist'] = (ema_fast - ema_slow) - (ema_fast - ema_slow).ewm(span=9, adjust=False).mean()

# 3) 检测
divs = find_three_segment_divergences(df['hist'], df['low'], df['high'])

# 4) 看结果
for d in divs:
    print(d['kind'], 'Lv', d['level'],
          'ratio=', round(d['ratio'], 2),
          'at', df.index[d['s3_end']])
```

输出：

```
bullish Lv 1 ratio= 0.34 at 2022-07-18
bearish Lv 1 ratio= 0.41 at 2023-09-15
...
```

---

## API

```python
find_three_segment_divergences(
    hist_series,           # pd.Series  MACD 柱状图（正绿负红）
    low_series,            # pd.Series  K 线最低价
    high_series,           # pd.Series  K 线最高价
    min_bars=0,            # 每段最少 K 线根数（噪声过滤）
    ratio_threshold=0.5,   # 触发面积比阈值
    max_level=1,           # 1 = 基础三段；2 = 含 Lv2 (P+S4+S5)；None = 穷尽
    block_by_opposite=True # 是否启用反向屏障过滤
) -> list[dict]
```

每条返回 dict 的字段：

| 字段 | 含义 |
|------|------|
| `kind` | `'bullish'` 底背离 / `'bearish'` 顶背离 |
| `level` | 触发层级：1 = 基础三段，2+ = 分层扩展 |
| `s1_start`, `s1_end` | 左侧主体 P（**位置整数下标，不是时间戳**） |
| `s3_start`, `s3_end` | 最末同向段 S_last（`s3_end` 即触发点） |
| `s1_area`, `s3_area` | 面积（`|hist|` 累计） |
| `s1_bars`, `s2_bars`, `s3_bars` | 各段根数 |
| `ratio` | `s3_area / s1_area`，越小背离越强 |
| `provisional` | `True` = 末段尚未"封口"，需谨慎对待 |
| `same_terminal_l1` | `True` = 多尺度共振，信号更强 |

> **重要**：返回的 `*_start` / `*_end` 是**位置整数**，不是 DatetimeIndex 标签。用 `df.index[idx]` 还原时间戳。

---

## 示意图

```
   底背离 (bullish)                顶背离 (bearish)
   ───────────────                 ───────────────
                                                  ▼
        S1            S3              S1          S3
        ▓▓            ▒                ░░          ░
        ▓▓    S2      ▒                ░░    S2    ░
   ─────▓▓────░──────▒───            ──░░────▓────░───
                                                    
        ▲                                           
   ratio = S3.area / S1.area  <  0.5               
   且 价格创新低 / 创新高                            
```

---

## 常用模式

```python
# 默认：基础三段 + 屏障过滤
divs = find_three_segment_divergences(df['hist'], df['low'], df['high'])

# 多尺度分析（找趋势级别背离）
divs = find_three_segment_divergences(df['hist'], df['low'], df['high'], max_level=3)

# 回测：过滤掉暂定信号
confirmed = [d for d in divs if not d['provisional']]

# 调试：拿全部原始候选
all_cands = find_three_segment_divergences(
    df['hist'], df['low'], df['high'],
    max_level=None, block_by_opposite=False,
)
```

---

## 参数调优速查

| 参数 | 默认 | 何时调整 |
|------|------|----------|
| `ratio_threshold` | 0.5 | 更严：0.3~0.4；更宽：0.6~0.7 |
| `max_level` | 1 | 日线：2~3；周线：2~None；小时级以下：保持 1 |
| `min_bars` | 0 | 日线：1~3；小时级：3~5；分钟级：5+ |
| `block_by_opposite` | True | 实战中始终保持 True，仅调试时关闭 |

---

## 文档

- **完整使用教程**：见 [TUTORIAL_ZH.md](TUTORIAL_ZH.md) ——
  包含输入契约、参数调优依据、可视化样例、5 个常见坑。
- **算法规范**：见 `divergence.py` 模块顶部的 docstring ——
  这是公理化结构、分层扩展语义、反向屏障规则的权威参考。

---

## 协议

MIT。
