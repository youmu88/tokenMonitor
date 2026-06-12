#!/bin/bash
# ============================================================
# Token Monitor - macOS 状态栏应用一键安装脚本（增强版）
# ============================================================
# 用法: bash install.sh [--pack]
#   --pack: 同时打包为 .app 捆绑包（需要 py2app）
# 功能: 安装依赖、创建虚拟环境、配置开机自启、启动应用
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="TokenStatusApp"
VENV_DIR="$SCRIPT_DIR/.venv"
PLIST_NAME="com.token.monitor"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
LOG_FILE="$SCRIPT_DIR/token_monitor.log"
PACK_FLAG=false

# 解析参数
for arg in "$@"; do
    case "$arg" in
        --pack) PACK_FLAG=true ;;
    esac
done

echo "=========================================="
echo "  Token Monitor - macOS 状态栏应用安装"
echo "=========================================="
echo ""

# ---- 步骤 1: 检查 Python ----
echo "[1/6] 🔍 检查 Python 环境..."
PYTHON=""
for cmd in python3 python3.11 python3.12 python3.13 python3.14; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "❌ 未找到 Python3，请先安装: brew install python"
    exit 1
fi
echo "   ✅ 使用: $($PYTHON --version)"

# ---- 步骤 2: 创建虚拟环境 ----
echo "[2/6] 📦 创建 Python 虚拟环境..."
if [ -d "$VENV_DIR" ]; then
    echo "   ⚠ 虚拟环境已存在，跳过创建"
else
    $PYTHON -m venv "$VENV_DIR"
    echo "   ✅ 虚拟环境已创建: $VENV_DIR"
fi

# 激活虚拟环境
source "$VENV_DIR/bin/activate"
echo "   ✅ 虚拟环境已激活"

# ---- 步骤 3: 安装依赖 ----
echo "[3/6] 📥 安装依赖..."
pip install --upgrade pip -q
pip install playwright rumps pyobjc-core pyobjc-framework-Cocoa pyobjc-framework-Quartz py2app -q
echo "   ✅ Python 依赖安装完成"

# 安装 Playwright 浏览器
echo "   🔧 安装 Playwright Chromium 浏览器..."
python -m playwright install chromium 2>&1 | tail -3
echo "   ✅ Playwright Chromium 安装完成"

# ---- 步骤 4: 检查/创建 cookie 文件 ----
echo "[4/6] 🔑 检查认证状态..."
if [ -f "$SCRIPT_DIR/.token_monitor_cookies.json" ]; then
    echo "   ✅ 已检测到保存的登录凭证"
else
    echo "   ⚠ 未检测到登录凭证"
    echo "   ℹ️  首次运行时会自动打开浏览器引导登录"
fi

# ---- 步骤 5: 配置开机自启（LaunchAgent） ----
echo "[5/6] ⚙️  配置开机自启..."

# 确保 LaunchAgents 目录存在
mkdir -p "$HOME/Library/LaunchAgents"

# 生成 plist 文件
cat > "$PLIST_PATH" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${VENV_DIR}/bin/python</string>
        <string>${SCRIPT_DIR}/token_status_app.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>
    <key>StandardOutPath</key>
    <string>${LOG_FILE}</string>
    <key>StandardErrorPath</key>
    <string>${LOG_FILE}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>ThrottleInterval</key>
    <integer>60</integer>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>${VENV_DIR}/bin:/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
PLISTEOF

echo "   ✅ LaunchAgent plist 已创建: $PLIST_PATH"

# 卸载旧的（如果有）
launchctl bootout "gui/$(id -u)/${PLIST_NAME}" 2>/dev/null || true

# 加载新的
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || launchctl load "$PLIST_PATH" 2>/dev/null || true
echo "   ✅ 开机自启已配置"

# ---- 步骤 6: 启动应用 ----
echo "[6/6] 🚀 启动 Token 状态栏应用..."
echo ""
echo "=========================================="
echo "  ✅ 安装完成！"
echo "=========================================="
echo ""
echo "📌 应用已启动，请在 macOS 状态栏查看 Token 监控信息"
echo "📌 状态栏图标: 动态进度条图标（PyObjC 原生渲染）"
echo "📌 数据持久化: SQLite 历史记录（保留30天）"
echo "📌 日志文件: $LOG_FILE"
echo "📌 配置文件: $SCRIPT_DIR/.token_monitor_cookies.json"
echo ""

# 如果指定了 --pack 参数，尝试打包为 .app
if [ "$PACK_FLAG" = true ]; then
    echo "📦 正在打包为 .app 捆绑包..."
    cd "$SCRIPT_DIR"
    python setup.py py2app 2>&1 | tail -5
    APP_BUNDLE="$SCRIPT_DIR/dist/TokenMonitor.app"
    if [ -d "$APP_BUNDLE" ]; then
        echo "   ✅ .app 捆绑包已创建: $APP_BUNDLE"
        echo "   💡 双击即可运行，无需终端"
        # 复制到 Applications 目录
        cp -R "$APP_BUNDLE" "/Applications/TokenMonitor.app" 2>/dev/null && \
            echo "   ✅ 已复制到 /Applications/TokenMonitor.app" || \
            echo "   ⚠ 复制到 /Applications 失败（权限不足），请手动复制:"
        echo "      sudo cp -R $APP_BUNDLE /Applications/"
    else
        echo "   ⚠ 打包失败，请查看上方错误信息"
    fi
fi

echo "💡 操作提示:"
echo "   - 点击状态栏图标查看详情和操作菜单"
echo "   - 菜单包含: 刷新 / 历史趋势折线图 / 统计信息 / 打开网页 / 重新登录"
echo "   - 首次运行会自动打开浏览器引导登录"
echo "   - 如需手动登录: cd $SCRIPT_DIR && source .venv/bin/activate && python token_monitor.py --login"
echo "   - 如需停止: launchctl bootout gui/$(id -u)/${PLIST_NAME}"
echo "   - 如需打包为 .app: cd $SCRIPT_DIR && source .venv/bin/activate && python setup.py py2app"
echo ""

# 启动应用：LaunchAgent 已通过 RunAtLoad 启动，避免再 nohup 启动造成双实例。
echo "   ✅ 应用将由 LaunchAgent 启动"
echo "   ℹ️  如果这是首次运行，浏览器将自动打开，请登录 OA 系统"
echo "   ℹ️  查看日志: tail -20 $LOG_FILE"

echo ""
echo "=========================================="