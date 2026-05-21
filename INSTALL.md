# 安装

uniark-pro 需要 **Python 3.9 或更新版本**,必须在虚拟环境(venv)中安装。

> **关于 venv 的说明**
>
> 自 2023 年起(Python PEP 668),macOS、Ubuntu 23.04+、Debian 12+ 都默认禁止 `pip install` 直接写入系统 Python——因为系统 Python 是给操作系统自己用的,污染它可能导致系统工具失灵。venv 不是项目的额外要求,是现代 Python 的通用做法:**每个项目一个独立环境,依赖互不干扰**。

---

## macOS

### 准备工作

1. 安装 Xcode Command Line Tools(自带 Python 3.9):

   ```bash
   xcode-select --install
   ```

2. ⚠️ **重要:不要把项目放在 `~/Desktop` 或 `~/Documents` 下。**

   如果开启了「桌面与文稿同步到 iCloud Drive」(macOS 默认勾选),这两个目录其实跑在 iCloud Drive 上,POSIX 文件锁支持不完整,会导致 yfinance / akshare / SQLite 等依赖文件锁的库随机抛出:

   ```
   [Errno 11] Resource deadlock avoided
   ```

   建议放在 home 根目录(`~/uniark-pro`)或专门的 `~/projects/` 下。

### 安装

```bash
cd ~                              # 或者 cd ~/projects/
mkdir uniark-pro && cd uniark-pro

# 下载源代码
curl -L https://github.com/uniark-pro/uniark-pro/tarball/main | tar -xz
mv */code_pro_zh/* .
rm -rf uniark-pro-uniark-pro-*
curl -LO https://raw.githubusercontent.com/uniark-pro/uniark-pro/main/requirements.txt

# 创建虚拟环境
python3 -m venv venv

# 激活虚拟环境(提示符前会出现 (venv) 标签)
source venv/bin/activate

# 安装依赖
pip install --upgrade pip
pip install -r requirements.txt
```

### 启动

```bash
cd ~/uniark-pro
source venv/bin/activate    # 每次新开终端都要重新激活
python3 app.py
```

浏览器打开 <http://localhost:5000>。

---

## Ubuntu / Debian

### 准备工作

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
```

> **注意:** `python3-venv` 在 Ubuntu/Debian 上是**单独的包**,必须显式安装,否则后面 `python3 -m venv` 会报 `The virtual environment was not created successfully`。

### 安装

```bash
cd ~
mkdir uniark-pro && cd uniark-pro

# 下载源代码
curl -L https://github.com/uniark-pro/uniark-pro/tarball/main | tar -xz
mv */code_pro_zh/* .
rm -rf uniark-pro-uniark-pro-*
curl -LO https://raw.githubusercontent.com/uniark-pro/uniark-pro/main/requirements.txt

# 创建虚拟环境
python3 -m venv venv

# 激活虚拟环境
source venv/bin/activate

# 安装依赖
pip install --upgrade pip
pip install -r requirements.txt
```

### 启动

```bash
cd ~/uniark-pro
source venv/bin/activate
python3 app.py
```

---

## 常见问题

### `error: externally-managed-environment`

说明 venv 没激活,pip 在试图写系统 Python。先执行 `source venv/bin/activate`,确认提示符前出现 `(venv)` 后再装。

### macOS 上偶发 `[Errno 11] Resource deadlock avoided`

项目放在 iCloud 同步目录(`~/Desktop` 或 `~/Documents`)下了。把项目移到 home 根目录,并**重建 venv**——venv 不能简单复制,里面有写死的绝对路径:

```bash
mv ~/Desktop/uniark-pro ~/uniark-pro
cd ~/uniark-pro
rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### macOS 上 yfinance 安装报 `No matching distribution found for curl_cffi`

苹果自带的 Python 是 3.9,而新版 yfinance 要求 Python 3.10+。`requirements.txt` 里已经把 yfinance 锁在了 0.2.x 系列,直接 `pip install -r requirements.txt` 不会触发这个问题。如果你单独 `pip install yfinance` 才会出现。

### 每次重开终端都要重新 `source venv/bin/activate` 吗?

是的。venv 是按 shell 会话激活的,关闭终端窗口或开新窗口都会失效。可以把 `cd ~/uniark-pro && source venv/bin/activate && python3 app.py` 存成一个启动脚本,方便日常使用。
