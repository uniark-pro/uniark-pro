# `divergence.py` 使用教程

> 本文是把 `divergence.py` 作为独立库使用的入门指南。模块顶部的 docstring 仍是
> 最终的规范文档；本文聚焦"怎么用、怎么解读结果、怎么调参、容易踩什么坑"。

---

## 0. 它做什么

`divergence.py` 在 MACD 柱状图（hist）上检测**三段背离结构**——也就是经典的
"S1（同向段）+ S2（反向段）+ S3（同向段）"模式，外加分层递归扩展（P+S4+S5、
P+S6+S7、……）和反向屏障过滤。

对外只暴露一个函数：

```python
find_three_segment_divergences(
    hist_series,           # pd.Series  MACD 柱状图
    low_series,            # pd.Series  K 线最低价
    high_series,           # pd.Series  K 线最高价
    min_bars=0,            # 段最少 K 线根数（噪声过滤）
    ratio_threshold=0.5,   # 面积比阈值
    max_level=1,           # 分层深度：1=基础三段，2=P+S4+S5，None=穷尽
    block_by_opposite=True # 是否应用反向屏障规则
)
```

返回 `list[dict]`，每条记录一个被检出的背离。

---

## 1. Quickstart：10 行跑出第一个结果

```python
import pandas as pd
from divergence import find_three_segment_divergences

# 1) 你有一份 OHLCV 数据。任何来源都行，只要列名是 open/high/low/close
df = pd.read_csv('btc_daily.csv', parse_dates=['date'], index_col='date')

# 2) 自己算 MACD（标准参数 12/26/9）
ema_fast = df['close'].ewm(span=12, adjust=False).mean()
ema_slow = df['close'].ewm(span=26, adjust=False).mean()
macd     = ema_fast - ema_slow
signal   = macd.ewm(span=9, adjust=False).mean()
df['hist'] = macd - signal

# 3) 调用检测函数
divs = find_three_segment_divergences(df['hist'], df['low'], df['high'])

# 4) 看结果
for d in divs:
    print(d['kind'], 'Lv', d['level'],
          'ratio=', round(d['ratio'], 2),
          'at', df.index[d['s3_start']], '→', df.index[d['s3_end']])
```

输出形如：

```
bullish Lv 1 ratio= 0.34 at 2022-06-10 → 2022-07-18
bearish Lv 1 ratio= 0.41 at 2023-08-02 → 2023-09-15
...
```

跑通了，下面解释每一块。

---

## 2. 输入契约（容易被忽略但很重要）

### 2.1 三个 Series 必须索引对齐、长度一致

`hist_series`、`low_series`、`high_series` 在函数内是**按位置取值**（`.iloc`）的。
传进来的三个 Series 必须是同一个 DataFrame 上同一行同一列对应的——也就是说，
最稳妥的做法永远是：

```python
divs = find_three_segment_divergences(df['hist'], df['low'], df['high'])
```

而不是从不同来源拼起来三个 Series。如果三者长度不等或对齐方式不同，结果就是错的，
而且函数不会报错——会静默给出无意义的结果。

### 2.2 返回的下标是**位置型整数**，不是 DatetimeIndex 标签

返回 dict 里的 `s1_start`、`s1_end`、`s3_start`、`s3_end` 都是**整数下标**
（0-based 的位置序号），而**不是**时间戳。要拿到时间，用 `df.index[idx]`：

```python
for d in divs:
    t_start = df.index[d['s3_start']]
    t_end   = df.index[d['s3_end']]
    print(f"Lv{d['level']} {d['kind']}: {t_start} ~ {t_end}")
```

**推论**：你传给函数的那份 hist 序列必须始终和你后续用来定位时间/价格的那份
DataFrame 是同一份。如果检测时传的是 `df['hist']`，后面解读时却用
`df.iloc[100:]['hist']`，下标就全错位了。

### 2.3 NaN 的处理

函数对 NaN 鲁棒，但有一个细节：**NaN 会终止当前段**——如果连续同号 hist
中间夹了一个 NaN，那两块会被切成两段而不是一段。在 warmup 期（MACD 的前
26 根附近）这不是问题，因为整片都是 NaN；但如果数据中间夹散落的 NaN，
就会造成段的碎片化。

最稳妥的做法是调用前 dropna：

```python
df = df.dropna(subset=['hist']).reset_index(drop=False)
# 注意：reset_index 后下标变成新的 0..N-1，时间戳作为列保留
```

或者：

```python
df = df.iloc[26:].copy()   # 跳过 MACD 还没稳定的前几行
```

### 2.4 数据要够"长"

每检测一层基础三段背离，至少需要 3 个段；要分层扩展到 Lv2，至少需要 5 个段。
段是由 hist 翻号切出来的，所以**实际需要的 K 线根数 = 段数 × 平均段长**。
日线上，背离从两三个月到一两年的跨度都有可能；要看到 Lv1 至少给 100~200 根，
要看到 Lv2 通常需要 300+ 根。

---

## 3. 解读返回结果

### 3.1 字段速查

| 字段 | 类型 | 含义 |
|------|------|------|
| `kind` | `'bullish'` / `'bearish'` | 底背离（看涨）/ 顶背离（看跌） |
| `level` | int | 触发层级。1 = 基础 S1+S2+S3；2 = P+S4+S5；3 = P+S6+S7；… |
| `s1_start` | int | **左侧主体 P 的起始位置**。Lv1 时即 S1 的起点；Lv2 时是 S1 起点（P 跨过 S1+S2+S3） |
| `s1_end` | int | 左侧主体 P 的结束位置 |
| `s3_start` | int | **最末同向段 S_last 的起点**。Lv1 时即 S3；Lv2 时即 S5 |
| `s3_end` | int | 最末同向段的结束位置——这就是"触发点" |
| `s1_area` | float | 左侧主体面积之和（只算同向段，反向段不计） |
| `s3_area` | float | 最末同向段面积 |
| `s1_bars` | int | 左侧主体跨度根数（含中间反向段） |
| `s2_bars` | int | 紧邻最末同向段之前那段反向段的根数 |
| `s3_bars` | int | 最末同向段的根数 |
| `ratio` | float | `s3_area / s1_area`——这就是"力度衰减比例" |
| `provisional` | bool | True = 最末段还没"封口"（见 §3.2） |
| `same_terminal_l1` | bool | True = 这条 Lv≥2 的末端位置上 Lv1 也独立成立（见 §3.3） |

`ratio` 越小，背离越强：
- `ratio = 0.3` 表示 S_last 力度只有前面主体的 30%
- `ratio = 0.5` 是默认阈值的临界值（再大不会被检出）
- `ratio = 0.1` 是非常强的衰竭信号

`level` 反映"在多大尺度上发生衰竭"：
- Lv1 = 局部三段背离（短期趋势衰竭）
- Lv2 = 跨过一个反向段的更大结构衰竭（中期趋势衰竭）
- Lv3+ = 跨越多个中间反弹/调整的长期趋势衰竭

### 3.2 `provisional`：暂定信号 vs 确定信号

最末段 S_last 的右端点等于 hist 序列末尾时，`provisional=True`。语义是：

> 这段还没"封口"——未来如果 hist 还是同符号，这段会继续延伸；只有翻号了，
> 这段才定型。现在算出的 `ratio` 只是快照而非判决。

实战含义：
- `provisional=False`：信号已经定型，是历史确定信号（用于回测、统计）
- `provisional=True`：当前仍在形成中，触发时间和力度都可能改变（用于实时盯盘）

⚠️ 一个常见的坑：跑回测时如果不过滤 `provisional`，你会把"最后一根 K 线上的暂定信号"
当成确定信号，造成 lookahead bias 错觉。建议：

```python
confirmed = [d for d in divs if not d['provisional']]
provisional = [d for d in divs if d['provisional']]
```

### 3.3 `same_terminal_l1`：多尺度共振

仅在 `level >= 2` 的记录上**可能**为 True。语义是：

> 这条 Lvk 背离的末端位置 (s3_start, s3_end) 上，单独看 S_last 与紧邻前一同向段的
> 面积比，Lv1 也独立成立（< ratio_threshold）。

这两个条件互不蕴含——Lvk 的 `S_last / (前面所有同向段累计)` 小，不代表 Lv1 的
`S_last / 紧邻前一段` 也小。两者同时成立表示**"力度衰竭在多个尺度上同时发生"**，
经验上是更强的信号。

UI 通常据此画"双三角"标记（vs 单三角）。如果你只关心二元信号，这个字段可以忽略；
如果做信号强度分级，可以把它纳入打分。

---

## 4. 典型调用模式

### 4.1 默认模式（向后兼容）

```python
divs = find_three_segment_divergences(df['hist'], df['low'], df['high'])
# 等价于：
# min_bars=0, ratio_threshold=0.5, max_level=1, block_by_opposite=True
```

只检测基础三段背离 + 应用屏障过滤。这是日常分析最常用的配置。

### 4.2 开启分层扩展（找趋势级别的背离）

```python
divs = find_three_segment_divergences(
    df['hist'], df['low'], df['high'],
    max_level=2,   # 同时找 Lv1 和 Lv2
)
```

或穷尽所有可能层级：

```python
divs = find_three_segment_divergences(
    df['hist'], df['low'], df['high'],
    max_level=None,  # 让算法决定
)
```

`max_level=None` 在很长的序列（>500 根）上会探索很多高阶组合，多数会被屏障过滤掉
最终保留 Lv2~Lv3 居多。设个保守上限（比如 `max_level=3`）通常足够。

### 4.3 调试模式：看全部原始候选

```python
all_candidates = find_three_segment_divergences(
    df['hist'], df['low'], df['high'],
    max_level=None,
    block_by_opposite=False,   # ← 关掉屏障过滤
)
```

这会返回**所有**通过面积比和价格新极值测试的候选——包括会被屏障 D' 否决的那些。
用来：
- 验证屏障规则是否如预期工作
- 复现旧版本行为（block_by_opposite 是后加的）
- 给信号"打候选池"，自己再做下游过滤

### 4.4 噪声过滤：`min_bars`

```python
divs = find_three_segment_divergences(
    df['hist'], df['low'], df['high'],
    min_bars=3,    # 不足 3 根的段被并入相邻反向段
)
```

`min_bars` 只对**第 1 层**的最小段过滤生效（高层 P 是复合段，bars 必然较大，没必要再过滤）。

经验值：
- `min_bars=0`（默认）：完全不过滤。最敏感，也最噪声。
- `min_bars=2~3`：日线、3 日线、周线常用。
- `min_bars=5+`：噪声非常大的小时级以下，可以拉高。

注意：`min_bars=0` 时函数内的合并步骤是 no-op；不为 0 时会主动改写段序列，
某些"看上去是三段"的形态可能被合并掉而不再触发——这是算法语义而非 bug。

---

## 5. 最小可视化配方

如果你想自己画图（不用项目里的 plot_helpers），下面是一个最小实现：

```python
import matplotlib.pyplot as plt

def plot_divergences(df, divs):
    fig, (ax_price, ax_macd) = plt.subplots(
        2, 1, figsize=(14, 8), sharex=True,
        gridspec_kw={'height_ratios': [3, 1]}
    )

    # 上方：收盘价
    ax_price.plot(df.index, df['close'], color='black', linewidth=1)
    ax_price.set_title('Price')

    # 下方：MACD hist
    colors = ['#cc3333' if h < 0 else '#33aa33' for h in df['hist']]
    ax_macd.bar(df.index, df['hist'], color=colors, width=0.8)
    ax_macd.axhline(0, color='gray', linewidth=0.5)
    ax_macd.set_title('MACD Histogram')

    # 标注背离
    for d in divs:
        x_mid_idx = (d['s3_start'] + d['s3_end']) // 2
        x_mid = df.index[x_mid_idx]
        ratio_pct = d['ratio'] * 100

        if d['kind'] == 'bullish':
            color = '#1e90ff' if d['provisional'] else '#ff3344'
            y = df['hist'].iloc[d['s3_start']:d['s3_end']+1].min()
            marker = '^'
            text_y_offset = -abs(y) * 0.5
        else:
            color = '#1e90ff' if d['provisional'] else '#22aa44'
            y = df['hist'].iloc[d['s3_start']:d['s3_end']+1].max()
            marker = 'v'
            text_y_offset = abs(y) * 0.5

        # 双三角（多尺度共振）
        if d.get('same_terminal_l1', False):
            ax_macd.scatter([x_mid, x_mid], [y, y], marker=marker,
                            s=[100, 60], color=color, zorder=5)
        else:
            ax_macd.scatter([x_mid], [y], marker=marker,
                            s=80, color=color, zorder=5)

        suffix = ' ?' if d['provisional'] else ''
        label = f"L{d['level']}\n{ratio_pct:.0f}%{suffix}"
        ax_macd.annotate(label, xy=(x_mid, y),
                         xytext=(0, text_y_offset),
                         textcoords='offset points',
                         ha='center', va='center',
                         fontsize=8, color=color)

    plt.tight_layout()
    return fig

fig = plot_divergences(df, divs)
fig.savefig('divs.png', dpi=120)
```

关键细节：
- 箭头 x 坐标取 `(s3_start + s3_end) / 2`，落在末段中点
- 箭头 y 坐标贴 hist 极值柱（底背离=最深红柱，顶背离=最高绿柱）
- `provisional=True` 时把文字颜色切到蓝色，加 `?` 后缀
- `same_terminal_l1=True` 时画双三角（两个 marker 错开叠）

如果你直接用项目自带的 `plot_helpers.annotate_divergences`，把它接到 mplfinance
或自定义 matplotlib axes 上即可——它的签名是 `(macd_ax, df, divergences)`，
约束就是 `df` 必须有 `'hist'` 列且下标和检测时一致。

---

## 6. 参数调优建议

### 6.1 `ratio_threshold`（默认 0.5）

控制"多弱的衰减算背离"。
- `0.5` 是经验默认值，平衡敏感度和误报。
- `0.3~0.4`：更严格。只检出明显衰竭，假阳性低但会漏。
- `0.6~0.7`：更宽松。包含轻微衰减，假阳性高。

**不建议超过 0.7**——超过的话语义上就不算"衰竭"了。

### 6.2 `max_level`

| 周期 | 推荐 | 理由 |
|------|------|------|
| 小时级、分钟级 | `1` | 数据噪声大，高阶背离往往是巧合 |
| 日线 | `2` 或 `3` | 标准多尺度分析的甜点 |
| 周线、3 日线 | `2` ~ `None` | 数据少噪声小，可以放开 |

### 6.3 `min_bars`

经验上跟周期反相关——周期越短，市场噪声越大，`min_bars` 越要拉高：

| 周期 | 推荐 `min_bars` |
|------|-----------------|
| 周线 | 0 ~ 1 |
| 日线、3 日线 | 1 ~ 3 |
| 4h、1h | 3 ~ 5 |
| 30m、15m | 5+ |

### 6.4 `block_by_opposite`

实战中应当**始终保持 True**。`False` 会绕过核心语义：基于公理
"**触发反向背离 = 下一个同向结构的起点**"，一个新结构若在形成过程中
跨过另一个已触发的反向背离，则被否决（应用层进一步要求屏障方至少
是 L≥2，避免 L1+L1 双重转折信号被互相屏蔽）。

`False` 的合理用途只有：
- 调试算法本身
- 输出"候选池"给下游做自定义过滤
- 复现没有屏障规则的旧版本行为

---

## 7. 常见坑

### 坑 1：下标错位

```python
# ❌ 错误用法
df_recent = df.iloc[-200:]   # 取最近 200 根
divs = find_three_segment_divergences(df['hist'], df['low'], df['high'])
                                     # ↑ 全量数据上检测
for d in divs:
    print(df_recent.index[d['s3_start']])   # 💥 索引错位
```

```python
# ✅ 正确用法（要么全部用全量，要么全部用切片）
df_recent = df.iloc[-200:].reset_index().rename(columns={'index': 'date'})
# 或直接：
divs = find_three_segment_divergences(
    df_recent['hist'], df_recent['low'], df_recent['high']
)
```

### 坑 2：`provisional` 在回测中没过滤

```python
# ❌ 隐含 lookahead bias
total_signals = len(divs)
```

```python
# ✅ 回测/统计场景只用确定信号
confirmed = [d for d in divs if not d['provisional']]
```

### 坑 3：把 `s3_end` 当作"信号生效时间"用错

`s3_end` 是 hist 最末段定型的那根 K 线——这是**检测时刻**而非"反转启动时刻"。
实际的价格反转可能在 `s3_end + 1, +2, +N` 才发生（或者根本不发生——背离也会失败）。

下游做交易信号时建议：
- 入场用 `s3_end` 之后的某个触发条件（突破前期低点等）
- 不要把 `s3_end` 那根 K 线的 close 当作入场价

### 坑 4：MACD 参数和你预期的不一致

本模块对 MACD 参数无任何假设——你传什么 hist 它就算什么。如果你用的不是
标准的 (12, 26, 9) 而是 (5, 35, 5) 之类的自定义参数，本模块照样工作，
但 `ratio_threshold=0.5` 的经验值就未必适用了（你得在自己的参数组合上重新校准）。

### 坑 5：周末 / 节假日造成"假翻号"

加密货币 24/7 连续无此问题。但股票上，节假日跨度产生的"日线跳空"有时让
本来连续同号的 hist 在跨节假日时被切成两段。如果发现节假日附近的背离判定
和直觉不符，检查一下原始 hist 序列，必要时用 `min_bars` 把它合并掉。

---

## 8. 进阶：直接调用 `find_hist_segments`

如果你想绕过整套背离逻辑，单纯利用本模块的"按符号切段"工具：

```python
from divergence import find_hist_segments

segs = find_hist_segments(df['hist'])
# 返回 list[dict]，每个 dict:
#   { 'sign': 'pos'|'neg', 'start': int, 'end': int,
#     'area': float, 'bars': int }
```

可用于：
- 自定义你自己的多段比较逻辑
- 计算 hist 序列的"段长分布"，给 `min_bars` 选个合理值
- 可视化"段"本身（每段画一个色块）

`find_hist_segments` 是函数式纯的，无副作用，可以放心当工具用。

---

## 附录：完整调用样例

把上面的所有部分串起来：

```python
import pandas as pd
import matplotlib.pyplot as plt
from divergence import find_three_segment_divergences

# === 1. 准备数据 ===
df = pd.read_csv('btc_daily.csv', parse_dates=['date'], index_col='date')

# === 2. 算 MACD ===
def add_macd(df, fast=12, slow=26, signal=9):
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    sig  = macd.ewm(span=signal, adjust=False).mean()
    df['hist'] = macd - sig
    return df

df = add_macd(df).dropna(subset=['hist'])

# === 3. 检测背离 ===
divs = find_three_segment_divergences(
    df['hist'], df['low'], df['high'],
    min_bars=2,
    ratio_threshold=0.5,
    max_level=2,
    block_by_opposite=True,
)

# === 4. 拆分确定 / 暂定 ===
confirmed   = [d for d in divs if not d['provisional']]
provisional = [d for d in divs if d['provisional']]

print(f"确定信号 {len(confirmed)} 条，暂定信号 {len(provisional)} 条")

# === 5. 输出每条信号 ===
for d in divs:
    t_start = df.index[d['s3_start']]
    t_end   = df.index[d['s3_end']]
    tag     = '?' if d['provisional'] else ''
    co      = '+L1' if d.get('same_terminal_l1') else ''
    print(f"[{d['kind']:8s} Lv{d['level']}{tag}] "
          f"{t_start.date()} ~ {t_end.date()} "
          f"ratio={d['ratio']*100:.0f}% {co}")

# === 6. 可视化（用项目自带的 plot_helpers 或自己画）===
# 见 §5
```

---

## 速查总结

| 任务 | 调用 |
|------|------|
| 日常用：找基础三段背离 | 全部默认参数 |
| 多尺度分析：找趋势级别背离 | `max_level=2` 或 `3` |
| 噪声大的小时线 | `min_bars=3`，`max_level=1` |
| 实时盯盘 | 全部默认；按 `provisional` 区分对待 |
| 回测：避免 lookahead bias | 过滤掉 `provisional=True` |
| 调试 / 复现旧行为 | `block_by_opposite=False` |
| 信号强度打分 | 用 `ratio`、`level`、`same_terminal_l1` 综合 |

---

**最后一句话**：本教程是"上手指南"，模块顶部的 docstring 和各内部函数的 docstring
才是**权威规范**。如果两者出现冲突，以代码为准。
