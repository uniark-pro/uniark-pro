# 公理化交易体系——多市场可视化工具(Pro 版)

这是基础版的多市场扩展。在原有加密货币的基础上,加入了**美股、A 股、
港股**三大股票市场,让同一套结构分析框架可以跨市场使用。

> **基础版仓库:** [f0133833/uniark-trading-system](https://github.com/f0133833/uniark-trading-system) —— 理论文档、库 API、可视化标记的详细说明都在那里。
> **本仓库:** 在基础版之上的私人扩展版本,作为朋友间分享的工具发布。

---

## 与基础版的关系

- **核心算法相同** —— 三段结构识别、分层背离、反向屏障规则,完全
  沿用基础版 `divergence.py` 的实现。算法的权威说明仍然在基础版的
  [docs/THEORY_ZH.md](https://github.com/f0133833/uniark-trading-system/blob/main/docs/THEORY_ZH.md) 和
  [docs/LIBRARY_ZH.md](https://github.com/f0133833/uniark-trading-system/blob/main/docs/LIBRARY_ZH.md)。
- **数据层重写** —— `data.py` 改造成路由层,按市场分发到不同 adapter。
  股票市场带 fallback 链,主源 yfinance 拉不到时自动切到 akshare。
- **预置品种和时段** —— `user_settings.json` 里每个市场预置了典型
  品种(腾讯、阿里、茅台、英伟达 ...)和常用时段,开箱即用。

## 这个项目是什么 / 不是什么

定位与基础版一致:

- **是** 一套面向长周期(日线及以上)持有者的结构分析可视化工具。
- **不是** 交易信号源,不是自动化交易系统,不是经过回测的策略,
  不是高频交易工具。

具体的边界说明详见基础版 README。

## 维护说明

本项目是给朋友圈分享的私人工具,不对外承担维护义务。

- 没有 Issues / PR 流程 —— 有问题直接联系我。
- 没有更新承诺 —— 代码按我自己的节奏迭代。
- 没有兼容性保证 —— Yahoo Finance 和 akshare 都依赖各自上游服务,
  上游 API 变动 / 反爬升级时数据获取可能失败,届时酌情修复。

---

## 安装

需要 Python 3.9 或更新版本。

```bash
mkdir uniark-pro && cd uniark-pro
curl -L https://github.com/uniark-pro/uniark-pro/tarball/main | tar -xz
mv */code_pro_zh/* .
rm -rf uniark-pro-uniark-pro-*
curl -LO https://raw.githubusercontent.com/uniark-pro/uniark-pro/main/requirements.txt
pip install -r requirements.txt
```

> 命令在 Linux(GNU tar)和 macOS(BSD tar)的 bash、zsh 下测试通过。
> Windows 用户请在 WSL 或 Git Bash 中执行。

### 强烈建议加装 akshare

```bash
pip install akshare
```

`requirements.txt` 默认**没有**列入 akshare,因为它依赖较重,且大多
数情况下 yfinance 就够用。但实际经验是:

- **新上市股**(尤其港股,例如 `0100.HK` MiniMax):yfinance 经常
  只返 1 根 K 线,无法画图。akshare 走中文数据源(新浪/东财/腾讯),
  能拉到全历史。
- **A 股**:yfinance 对 A 股的数据质量参差,akshare 是更稳的备选。
- **yfinance 偶尔反爬触发限频**:有 akshare 作为后备,体验更顺。

`data.py` 已经实现了"主源不足 30 根 → 自动切到备源"的逻辑,装上
akshare 后这条 fallback 链就活了。装不装是建议,不装也能跑(yfinance
单源工作)。

完整依赖:

- `python-binance` —— Binance 加密货币数据
- `yfinance` —— 美股 / A 股 / 港股 主数据源(Yahoo Finance)
- `akshare` —— A 股 / 港股 / 美股 备数据源(强烈建议)
- `pandas`、`numpy` —— 数据处理
- `mplfinance`、`matplotlib` —— 图表渲染
- `flask` —— Web UI(可选,仅 `app.py` 需要)
- `tkinter` —— 桌面 UI(通常随 Python 自带;Linux 上有时需要
  `sudo apt install python3-tk`)

---

## 使用方法

安装完成后,文件都在 `uniark-pro/` 目录下,从该目录运行:

### 桌面 UI

```bash
python main.py
```

### Web UI

```bash
python app.py
```

然后浏览器打开 `http://127.0.0.1:5000`。

### 工作流

1. 选择**市场**:加密货币 / 美股 / A 股 / 港股
2. 从该市场的预置品种里选一个(每个市场 5–6 个典型标的)
3. 选**入口周期**:周线 / 3 日线(仅加密) / 日线
4. 选**时段**(每个市场有针对性的预置区间)
5. 点"生成图表" —— 生成的 PNG 自动打开
6. 进入钻取模式,顶层每一段都可点击,以更细的周期渲染其子段

### 四个市场的标的格式

| 市场 | `market` 值 | 标的代码格式 | 示例(预置) |
|------|------------|-------------|------------|
| 加密货币 | `crypto` | Binance 交易对 | `BTCUSDT`、`ETHUSDT`、`SOLUSDT` |
| 美股 | `us_stock` | 纯 ticker | `NVDA`、`AAPL`、`TSLA`、`MU`、`MSFT` |
| A 股 | `cn_stock` | `代码.SS` / `代码.SZ` | `600519.SS`(茅台)、`300750.SZ`(宁德时代) |
| 港股 | `hk_stock` | `代码.HK`(4 位数字) | `0700.HK`(腾讯)、`9988.HK`(阿里)、`3690.HK`(美团) |

A 股的 `.SS` 表示上交所,`.SZ` 表示深交所;这是 Yahoo Finance 的命名
约定,akshare 内部会自动转换,使用上无差别。

### 想加入预置之外的标的

编辑 `user_settings.json`,在对应市场的 `symbols` 数组里加一条
`{"ticker": "...", "cn_name": "..."}` 即可。同理可以增/改时段预设。

---

## 入门教程

第一次使用工具,或者刚把它分享给朋友时,可能会困惑:

> "周线看着明明在涨,日线却一直提示卖出。听不听?"

这是大小周期的信号矛盾,也是这套工具最该被理解的地方。教程用 MU 和
BTC 两个真实案例,演示如何用"背离钻取"功能把矛盾解开:

📖 [**明明在涨,为什么系统提示卖?**](docs/tutorial/%E6%98%8E%E6%98%8E%E5%9C%A8%E6%B6%A8_%E4%B8%BA%E4%BB%80%E4%B9%88%E7%B3%BB%E7%BB%9F%E6%8F%90%E7%A4%BA%E5%8D%96.md)
—— 用"背离钻取"破解大小周期的信号矛盾

读完大概 10 分钟。读完后能回答:

- 周线没标卖出 / 日线密集卖出,哪个该听?
- `?` 号的信号能不能作为决策依据?
- 红色 MA99 染色是什么意思,怎么用?
- 子级别的同向 / 反向小信号怎么区分?

教程也有[微信公众号版](#)（链接待发布后填入）,内容相同,适合在手机上读
或转发给朋友。

---

## 数据源策略(简要)

`data.py` 是路由层,每个 market 对应一条 adapter 链:

```
crypto    → data_binance(无 fallback)
us_stock  → data_yfinance → data_akshare
cn_stock  → data_yfinance → data_akshare
hk_stock  → data_yfinance → data_akshare
```

主 adapter 返回 < 30 根 K 线时,自动切到备用 adapter。30 根这个阈值
是 MA99 和 MACD 都算不准的红线 —— 低于这个量级基本可以判定是数据
获取失败,而不是历史确实不够长。

返回的 DataFrame 的 `df.attrs['source']` 会标记实际命中的数据源,
图表标题里会一并显示,方便溯源。

---

## 可视化标记

与基础版完全一致:

| 标记 | 含义 |
|------|------|
| ▲ 红 | 底背离(潜在向上反转) |
| ▼ 绿 | 顶背离(潜在向下反转) |
| 双三角 | 力度衰竭在多尺度同时成立(更强信号) |
| 蓝色文字 + `?` | 暂定信号 —— 末段可能继续延伸 |
| `L2`、`L3` ... | 分层级别(L1 不显示) |
| 百分比 | 力度比 S<sub>last</sub> / Σ S<sub>同向段</sub> |

详细解读见基础版 README 的"可视化标记"一节。

---

## 项目结构

```
.
├── README.md                  ← 本文件(Pro 版说明)
├── LICENSE                    ← MIT 许可证
├── requirements.txt           ← Pro 版依赖(建议另装 akshare)
├── docs/
│   └── tutorial/              ← 入门教程
│       ├── 明明在涨_为什么系统提示卖.md
│       └── figures/           ← 教程配图
├── code_pro_zh/               ← Pro 版多市场源码(中文注释)← 主要使用这里
│   ├── data.py                ← 数据源路由 + fallback 链
│   ├── data_binance.py        ← Binance 加密货币 adapter
│   ├── data_yfinance.py       ← Yahoo Finance adapter(美股/A股/港股 主源)
│   ├── data_akshare.py        ← akshare adapter(股票市场备源)
│   ├── indicator.py           ← EMA、MACD 计算
│   ├── divergence.py          ← 核心算法(沿用基础版)
│   ├── plot_kline.py          ← K 线 + MACD 渲染
│   ├── plot_helpers.py        ← 背离标注辅助
│   ├── plot.py                ← 基础绘图(遗留)
│   ├── navigation.py          ← 钻取导航
│   ├── settings.py            ← 用户设置持久化
│   ├── settings_dialog_tk.py  ← Tkinter 设置对话框
│   ├── main.py                ← 桌面 UI 入口
│   ├── app.py                 ← Web UI 入口(Flask)
│   └── user_settings.json     ← 默认设置(4 市场预置品种与时段)
├── code_zh/                   ← 基础版中文注释源码(参考,fork 时继承)
├── code_en/                   ← 基础版英文注释源码(参考,fork 时继承)
└── images/                    ← 示例图(沿用基础版)
```

`code_pro_zh/` 是 Pro 版的入口,日常使用只看这一个目录就够了。
`code_zh/` 和 `code_en/` 是 fork 时从基础版继承下来的代码副本,
保留只是为了方便对照。

---

## 免责声明

本软件按"现状"提供,不附带任何形式的担保,具体见 MIT 许可证。
**本项目任何部分都不构成财务建议。** 加密货币、美股、A 股、港股的
交易都存在重大亏损风险;基于本软件做出的任何决策,使用者自行承担
全部责任。

本项目并未对所形式化的框架进行严格回测验证。任何想用它做实盘决策的
读者,强烈建议:

- 自行做统计验证(包含真实成本:滑点、手续费、印花税、汇率、税务)。
- 与买入持有等被动基准对比。
- 认识到"在历史图上视觉上很漂亮的标注"不意味着"在前向交易中能赚钱"。
- 特别提醒:跨市场使用时,不同市场的交易时段、波动率、流动性、
  税务规则、做空规则、停牌机制差异很大,基础版在加密货币上验证过的
  视觉直觉不一定能平移到股票市场。

---

## 许可证

[MIT 许可证](LICENSE),沿用基础版。你可以自由使用、修改、分发、
商用,需附带署名,不附带担保。

基础版作者:f0133833。本 fork 的修改部分:uniark-pro。

---

## 致谢

核心算法、理论框架、可视化设计全部来自基础版
[f0133833/uniark-trading-system](https://github.com/f0133833/uniark-trading-system)。
本 fork 的贡献是数据层重构和多市场扩展。

代码实现是在 Claude(Anthropic)作为编程协作者的帮助下完成的。
