# K线分析系统 · 多用户版

> 本文档说明**多用户版**的安装与使用方式。如果你只是自己一个人用，单用户版更简单，请参考原版 README。

---

## 与单用户版的区别

| 项目 | 单用户版 | 多用户版 |
|------|----------|----------|
| 启动方式 | `python3 app.py` | 同左，一个进程支持所有用户 |
| 访问方式 | 直接打开页面 | 先登录，再进入主界面 |
| 设置隔离 | 全局共用 `user_settings.json` | 每人独立 `user_settings_<用户名>.json` |
| 用户管理 | 无 | `manage_users.py` 命令行工具 |
| 新增文件 | — | `users.json`、`manage_users.py` |

多用户版对原有代码的改动**完全向下兼容**：`main.py`（桌面端）读写 `user_settings.json` 的行为不受影响。

---

## 文件结构

```
项目目录/
├── app.py                        # Web 后端（多用户版）
├── manage_users.py               # 用户管理命令行工具（新增）
├── settings.py                   # 设置读写（新增多用户函数）
├── users.json                    # 用户账号数据库（运行后自动生成）
├── user_settings_<用户名>.json   # 每位用户的个人设置（登录后自动生成）
├── main.py                       # 桌面端（不受多用户改动影响）
└── ...                           # 其余模块文件
```

---

## 快速开始

### 第一步：安装依赖

依赖与单用户版完全相同，无新增包：

```bash
pip install flask matplotlib pandas numpy binance akshare yfinance
```

### 第二步：创建用户

启动服务之前，先用 `manage_users.py` 添加至少一个用户：

```bash
python3 manage_users.py add alice  mypassword123
python3 manage_users.py add bob    anotherpass
```

查看已有用户：

```bash
python3 manage_users.py list
```

### 第三步：启动服务

```bash
python3 app.py
```

默认监听 `0.0.0.0:5000`，局域网内其他设备可通过 `http://<本机IP>:5000` 访问。

如需指定端口：

```bash
python3 app.py --port 8080
```

### 第四步：登录使用

浏览器打开地址后会看到登录页面，输入用户名和密码即可进入。每位用户的标的列表、时段配置完全独立，互不影响。

---

## 用户管理

所有操作通过 `manage_users.py` 完成，无需手动编辑 JSON 文件。

```bash
# 添加用户
python3 manage_users.py add <用户名> <密码>

# 列出所有用户及其 settings 文件状态
python3 manage_users.py list

# 修改密码
python3 manage_users.py passwd <用户名> <新密码>

# 删除用户（账号移除，settings 文件保留）
python3 manage_users.py delete <用户名>

# 查看某用户的 settings 文件路径
python3 manage_users.py show <用户名>
```

**用户名规则**：只能含字母、数字、下划线 `_`、连字符 `-`。

**密码存储**：密码以 SHA-256 哈希写入 `users.json`，明文不落盘。

---

## 安全建议

### 设置 Session 密钥

默认的 session 密钥仅供本地测试。如果服务对外网开放，建议通过环境变量设置一个随机字符串：

```bash
# 生成随机密钥（任选其一）
python3 -c "import secrets; print(secrets.token_hex(32))"
openssl rand -hex 32

# 启动时注入
KLINE_SECRET_KEY=你的随机字符串 python3 app.py
```

### 不建议的做法

- 不要把 `users.json` 提交到 Git 仓库（建议加入 `.gitignore`）。
- 不要把 `user_settings_*.json` 提交到仓库（含有个人分析配置）。
- 如果仅在局域网内使用，安全级别已足够；若暴露到公网，建议在前面加 Nginx 并启用 HTTPS。

### 推荐的 `.gitignore` 条目

```
users.json
user_settings_*.json
```

---

## 工作原理

```
浏览器访问任意路由
       │
       ▼
@before_request 检查 session
       │
  未登录 ──────────────▶ 重定向到 /login
       │
  已登录
       │
       ▼
读取 user_settings_<用户名>.json   ← 每人独立的标的和时段配置
       │
       ▼
渲染页面 / 执行请求
       │
       ▼
设置保存 ──▶ 写回 user_settings_<用户名>.json
```

用户首次登录时，若尚无个人 settings 文件，系统自动使用内置默认值（与单用户版初次启动行为相同）。用户在设置面板保存后，才会生成自己的 `user_settings_<用户名>.json`。

---

## 从单用户版迁移

如果你之前用单用户版已经保存了 `user_settings.json`，想把它作为某个用户的初始配置：

```bash
# 1. 添加用户
python3 manage_users.py add alice mypassword

# 2. 把旧的 settings 文件复制为该用户的专属文件
cp user_settings.json user_settings_alice.json
```

此后 alice 登录后会直接看到旧版本的标的和时段配置，无需重新设置。

---

## 常见问题

**Q：我只有一个人用，需要多用户版吗？**

不需要。单用户版更简单，直接 `python3 app.py` 打开就用，无需登录。

**Q：用户登录后修改设置，会影响其他用户吗？**

不会。每位用户的设置写入各自独立的 `user_settings_<用户名>.json`，完全隔离。

**Q：忘记密码怎么办？**

直接用 `passwd` 命令重置：

```bash
python3 manage_users.py passwd alice newpassword
```

**Q：能不能让某个用户使用另一个用户的配置？**

可以手动复制 settings 文件：

```bash
cp user_settings_alice.json user_settings_bob.json
```

之后两人的配置独立演进，不再互相影响。

**Q：`main.py` 桌面端还能用吗？**

可以，`main.py` 读写的是 `user_settings.json`（无用户名后缀），与多用户版的 Web 端完全独立，互不干扰。
