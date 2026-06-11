# Token Monitor - 内网 Token 使用情况监控工具

定时（默认 1 分钟）访问 `https://token.woa.com/`，自动抓取 token 使用情况。

## 🚀 快速开始（推荐：macOS 状态栏应用）

一键安装，在 macOS 状态栏显示 `Tokens: xxx/4060(yy%)`，后台自动监控并记录日志。

```bash
bash install.sh
```

安装脚本会自动完成：
1. ✅ 检查 Python 环境
2. ✅ 创建虚拟环境并安装依赖（playwright, rumps 等）
3. ✅ 安装 Playwright Chromium 浏览器
4. ✅ 配置开机自启（LaunchAgent）
5. ✅ 启动状态栏应用

**首次使用**：应用会自动打开浏览器引导您登录 OA 系统，登录成功后 cookie 自动保存，开始监控。

### 状态栏操作

| 操作 | 说明 |
|------|------|
| 点击状态栏图标 | 查看菜单 |
| 📊 Tokens: xxx/4060(yy%) | 当前使用情况（含进度条） |
| 🔄 立即刷新 | 手动触发一次抓取 |
| 📋 查看日志 | 用默认编辑器打开日志文件 |
| 🌐 打开网页 | 在浏览器中打开 token 监控页面 |
| 🔑 重新登录 | 清除 cookie 并重新登录 OA |
| ❌ 退出 | 退出状态栏应用 |

### 手动管理

```bash
# 停止应用
launchctl bootout gui/$(id -u)/com.token.monitor

# 启动应用
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.token.monitor.plist

# 查看日志
tail -f token_monitor.log

# 卸载
launchctl bootout gui/$(id -u)/com.token.monitor
rm -f ~/Library/LaunchAgents/com.token.monitor.plist
```

---

## 💻 命令行模式（备用）

如果不想使用状态栏应用，也可以使用命令行模式。

### 安装依赖

```bash
python3 -m venv venv
source venv/bin/activate
pip install playwright rich tabulate
playwright install chromium
```

### 运行

**方式一：CDP 模式（推荐，复用 Chrome 登录态）**

```bash
# 先关闭所有 Chrome 窗口，然后以调试模式启动 Chrome
bash start_chrome_debug.sh

# 在新终端窗口中运行监控
source venv/bin/activate
python3 token_monitor.py --cdp
```

**方式二：独立模式（启动新 Chrome 实例）**

```bash
# 先执行登录流程（会弹出浏览器窗口，手动登录 OA）
source venv/bin/activate
python3 token_monitor.py --login

# 登录成功后，启动监控
python3 token_monitor.py --standalone
```

### 命令行参数

| 参数 | 说明 |
|------|------|
| `--interval N` / `-i N` | 轮询间隔（秒），默认 60 |
| `--once` / `-o` | 只抓取一次后退出 |
| `--headless` | 无头模式（不显示浏览器窗口） |
| `--cdp` | CDP 模式，连接已运行的 Chrome（推荐） |
| `--standalone` | 独立模式，启动新 Chrome 实例 |
| `--login` | 仅执行登录流程，保存 cookie 后退出 |

### 示例

```bash
# 默认 60 秒轮询（自动检测模式）
python3 token_monitor.py

# 30 秒轮询
python3 token_monitor.py --interval 30

# 只抓取一次
python3 token_monitor.py --once

# 无头模式 + CDP
python3 token_monitor.py --cdp --headless

# 独立模式 + 30 秒间隔
python3 token_monitor.py --standalone --interval 30
```

---

## 📁 文件说明

| 文件 | 说明 |
|------|------|
| `token_status_app.py` | **macOS 状态栏应用**（rumps）— 推荐使用 |
| `token_monitor_core.py` | 核心抓取引擎（供状态栏应用和命令行共用） |
| `token_monitor.py` | 原命令行监控脚本（Rich 展示） |
| `install.sh` | **一键安装脚本** — 安装依赖 + 配置开机自启 + 启动 |
| `start_chrome_debug.sh` | 一键启动 Chrome 调试模式 |
| `.token_monitor_cookies.json` | 保存的登录 cookie |
| `token_monitor.log` | 监控日志文件 |

## 🔧 工作原理

1. 使用 Playwright 控制 Chrome 浏览器访问 `https://token.woa.com/`
2. 通过 CDP 连接复用已有 Chrome 的登录态，或使用保存的 cookie
3. 页面加载完成后，自动提取表格/JSON/文本数据
4. **状态栏模式**：在 macOS 状态栏显示 `Tokens: xxx/4060(yy%)`
5. **命令行模式**：在终端以 Rich 表格形式展示
6. 按设定间隔循环抓取，所有数据记录到日志文件

## 📝 日志

日志文件 `token_monitor.log` 记录每次抓取的结果和时间，格式：
```
2024-01-15 10:30:00 | INFO  | ✅ 抓取成功 | 已用: 1234/4060 (30.4%)
2024-01-15 10:31:00 | INFO  | ✅ 抓取成功 | 已用: 1240/4060 (30.5%)
```