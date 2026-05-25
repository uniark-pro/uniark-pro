# 公理化交易体系——多市场可视化工具(Pro 版)

这是基础版的多市场扩展。在原有加密货币的基础上,加入了**美股、A 股、
港股**三大股票市场,让同一套结构分析框架可以跨市场使用。

> **基础版仓库:** [f0133833/uniark-trading-system](https://github.com/f0133833/uniark-trading-system) —— 理论文档、库 API、可视化标记的详细说明都在那里。
> **本仓库:** 在基础版之上的私人扩展版本,作为朋友间分享的工具发布。

---

## 版本说明

本仓库目前维护两个版本,按需选用：

| 目录 | 版本 | 适合场景 |
|------|------|----------|
| `code_pro_zh/` | 单用户版 | 自己一个人用，直接运行，无需登录 |
| `code_pro_multiuser/` | **多用户版 v2.0.0** | 多人共用同一服务，各自独立设置 |

两个版本的核心算法、图表渲染、数据源完全相同，区别仅在于是否有登录认证。

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

需要 Python 3.9 或更新版本,**必须**使用虚拟环境(venv)。

### 单用户版（`code_pro_zh`）

```bash
mkdir uniark-pro && cd uniark-pro
curl -L https://github.com/uniark-pro/uniark-pro/tarball/main | tar -xz
mv */code_pro_zh/* .
rm -rf uniark-pro-uniark-pro-*
curl -LO https://raw.githubusercontent.com/uniark-pro/uniark-pro/main/requirements.txt

python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### 多用户版（`code_pro_multiuser`）

```bash
mkdir uniark-pro-multiuser && cd uniark-pro-multiuser
curl -L https://github.com/uniark-pro/uniark-pro/tarball/main | tar -xz
mv */code_pro_multiuser/* .
rm -rf uniark-pro-uniark-pro-*
curl -LO https://raw.githubusercontent.com/uniark-pro/uniark-pro/main/requirements.txt

python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 创建用户（至少创建一个才能登录）
python3 manage_users.py add 你的用户名 你的密码
```

每次新开终端使用项目前,需 `source venv/bin/activate` 激活虚拟环境。

📖 **多用户版详细说明 → [code_pro_multiuser/README_multiuser.md](code_pro_multiuser/README_multiuser.md)**

📖 **分系统的详细步骤、Ubuntu 准备工作、常见错误排查 →
[INSTALL.md](INSTALL.md)**

> **macOS 提示**:不要把项目放在 `~/Desktop` 或 `~/Documents` 下
> ——iCloud 同步会导致 SQLite 文件锁随机失败。建议放在 `~/uniark-pro`。

> 命令在 Linux(GNU tar)和 macOS(BSD tar)的 bash、zsh 下测试通过。
> Windows 用户请在 WSL 或 Git Bash 中执行。

完整依赖:

- `python-binance` —— Binance 加密货币数据
- `yfinance` —— 美股 / A 股 / 港股 主数据源(Yahoo Finance)
- `akshare` —— A 股 / 港股 / 美股 备数据源
- `pandas`、`numpy` —— 数据处理
- `mplfinance`、`matplotlib` —— 图表渲染
- `flask` —— Web UI(可选,仅 `app.py` 需要)
- `tkinter` —— 桌面 UI(通常随 Python 自带;Linux 上有时需要
  `sudo apt install python3-tk`)

---

## 使用方法

### 单用户版

安装完成后从 `code_pro_zh/` 目录运行：

```bash
# 桌面 UI
python main.py

# Web UI
python app.py
# 浏览器打开 http://127.0.0.1:5000
```

### 多用户版

安装完成后从 `code_pro_multiuser/` 目录运行：

```bash
# 创建用户（首次）
python3 manage_users.py add alice mypassword

# 启动服务
python3 app.py
# 浏览器打开 http://127.0.0.1:5000，输入用户名密码登录

# 指定端口（多人测试时）
python3 app.py --port 8080
```

多用户版的用户管理命令：

```bash
python3 manage_users.py list              # 列出所有用户
python3 manage_users.py add <用户名> <密码>   # 添加用户
python3 manage_users.py passwd <用户名> <新密码>  # 修改密码
python3 manage_users.py delete <用户名>    # 删除用户
```

### 工作流（两版相同）

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

---

## 入门教程

第一次使用工具,或者刚把它分享给朋友时,可能会困惑:

> "周线看着明明在涨,日线却一直提示卖出。听不听?"

这是大小周期的信号矛盾,也是这套工具最该被理解的地方。教程用 MU 和
BTC 两个真实案例,演示如何用"背离钻取"功能把矛盾解开:

📖 [**明明在涨,为什么系统提示卖?**](docs/tutorial/%E6%98%8E%E6%98%8E%E5%9C%A8%E6%B6%A8_%E4%B8%BA%E4%BB%80%E4%B9%88%E7%B3%BB%E7%BB%9F%E6%8F%90%E7%A4%BA%E5%8D%96.md)
—— 用"背离钻取"破解大小周期的信号矛盾

教程也有[微信公众号版](https://mp.weixin.qq.com/s/HzcyxRRc-11EJ3M0iiC1Ww),内容相同,适合在手机上读或转发给朋友。

---

## 完成之后

工具的部分写在了上面。但这两个月做这件事的过程,以及一些想通了
的事情,我另外写在了一篇文章里,分享给愿意听我聊聊的朋友:

📖 [**完成之后——写给朋友们的一些话**](docs/personal_reflection.md)
—— 关于妥协、死磕、和 AI 一起工作、以及"看见东西自己长出来"的体验

不是技术内容,只是一个朋友想说说他在想什么。

---

## 项目结构

```
.
├── README.md                      ← 本文件
├── INSTALL.md                     ← 详细安装说明
├── LICENSE                        ← MIT 许可证
├── requirements.txt               ← 依赖列表
├── docs/
│   ├── tutorial/                  ← 入门教程
│   └── 完成之后.md                ← 写给朋友们的心得文章
├── images/                        ← 示例图
├── code_pro_zh/                   ← 单用户版（主要使用这里）
│   ├── app.py                     ← Web UI 入口（Flask，无登录）
│   ├── main.py                    ← 桌面 UI 入口
│   ├── settings.py                ← 用户设置持久化
│   ├── user_settings.json         ← 默认设置（4 市场预置品种与时段）
│   └── ...                        ← 其余模块
├── code_pro_multiuser/            ← 多用户版 v2.0.0（新）
│   ├── app.py                     ← Web UI 入口（含登录认证）
│   ├── main.py                    ← 桌面 UI 入口（不受多用户影响）
│   ├── manage_users.py            ← 用户管理命令行工具
│   ├── settings.py                ← 设置持久化（含多用户函数）
│   ├── README_multiuser.md        ← 多用户版详细说明
│   └── ...                        ← 其余模块（与单用户版相同）
├── code_zh/                       ← 基础版中文注释源码（参考）
└── code_en/                       ← 基础版英文注释源码（参考）
```

---

## 数据源策略

`data.py` 是路由层,每个 market 对应一条 adapter 链:

```
crypto    → data_binance（无 fallback）
us_stock  → data_yfinance → data_akshare
cn_stock  → data_yfinance → data_akshare
hk_stock  → data_yfinance → data_akshare
```

主 adapter 返回 < 30 根 K 线时自动切到备用 adapter。

---

## 可视化标记

| 标记 | 含义 |
|------|------|
| ▲ 红 | 底背离（潜在向上反转） |
| ▼ 绿 | 顶背离（潜在向下反转） |
| 双三角 | 力度衰竭在多尺度同时成立（更强信号） |
| 蓝色文字 + `?` | 暂定信号 —— 末段可能继续延伸 |
| `L2`、`L3` ... | 分层级别（L1 不显示） |
| 百分比 | 力度比 S_last / Σ S_同向段 |

---

## 免责声明

本软件按"现状"提供,不附带任何形式的担保,具体见 MIT 许可证。
**本项目任何部分都不构成财务建议。** 加密货币、美股、A 股、港股的
交易都存在重大亏损风险;基于本软件做出的任何决策,使用者自行承担
全部责任。

---

## 许可证

[MIT 许可证](LICENSE),沿用基础版。你可以自由使用、修改、分发、
商用,需附带署名,不附带担保。

基础版作者：f0133833。本 fork 的修改部分：uniark-pro。

---

## 致谢

核心算法、理论框架、可视化设计全部来自基础版
[f0133833/uniark-trading-system](https://github.com/f0133833/uniark-trading-system)。
本 fork 的贡献是数据层重构、多市场扩展和多用户版升级。

代码实现是在 Claude（Anthropic）作为编程协作者的帮助下完成的。
