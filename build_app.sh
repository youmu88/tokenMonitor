#!/bin/bash
# ============================================================
# Token Monitor - 一键打包为 .app 捆绑包
# ============================================================
# 用法:
#   bash build_app.sh              # 仅打包到 dist/
#   bash build_app.sh --install    # 打包并安装到 /Applications
#   bash build_app.sh --dmg        # 打包并生成 DMG 安装镜像
#
# 打包产物:
#   dist/TokenMonitor.app          # .app 捆绑包
#   dist/TokenMonitor-1.0.0.dmg    # DMG 安装镜像（--dmg 时）
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/.venv"
APP_NAME="TokenMonitor"
VERSION="1.0.0"
DIST_DIR="$SCRIPT_DIR/dist"
BUILD_DIR="$SCRIPT_DIR/build"
APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
INSTALL_FLAG=false
DMG_FLAG=false

# ---- 颜色输出 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ---- 解析参数 ----
for arg in "$@"; do
    case "$arg" in
        --install) INSTALL_FLAG=true ;;
        --dmg)     DMG_FLAG=true ;;
        --help|-h)
            echo "用法: bash build_app.sh [--install] [--dmg]"
            echo "  --install  打包后安装到 /Applications"
            echo "  --dmg      打包后生成 DMG 安装镜像"
            exit 0
            ;;
    esac
done

echo ""
echo "=========================================="
echo "  Token Monitor - 一键打包 .app"
echo "=========================================="
echo ""

# ---- 步骤 1: 检查/创建虚拟环境 ----
info "步骤 1/5: 检查 Python 虚拟环境..."

PYTHON=""
if [ -f "$VENV_DIR/bin/python3" ]; then
    PYTHON="$VENV_DIR/bin/python3"
    ok "使用已有虚拟环境: $VENV_DIR"
else
    # 查找系统 Python
    for cmd in python3 python3.11 python3.12 python3.13 python3.14; do
        if command -v "$cmd" &>/dev/null; then
            PYTHON="$cmd"
            break
        fi
    done
    if [ -z "$PYTHON" ]; then
        err "未找到 Python3，请先安装: brew install python"
        exit 1
    fi
    info "创建虚拟环境..."
    $PYTHON -m venv "$VENV_DIR"
    PYTHON="$VENV_DIR/bin/python3"
    ok "虚拟环境已创建"
fi

# ---- 步骤 2: 安装/更新依赖 ----
info "步骤 2/5: 检查并安装依赖..."

# 激活虚拟环境
source "$VENV_DIR/bin/activate"

# 核心依赖列表
DEPS=(
    "rumps"
    "pyobjc-core"
    "pyobjc-framework-Cocoa"
    "pyobjc-framework-Quartz"
    "playwright"
    "py2app"
)

MISSING=()
for dep in "${DEPS[@]}"; do
    if ! python3 -c "import ${dep//-/_}" 2>/dev/null; then
        # 特殊处理带横线的包名
        pkg_name="$dep"
        case "$dep" in
            pyobjc-core) pkg_name="pyobjc-core" ;;
            pyobjc-framework-Cocoa) pkg_name="pyobjc-framework-Cocoa" ;;
            pyobjc-framework-Quartz) pkg_name="pyobjc-framework-Quartz" ;;
        esac
        MISSING+=("$pkg_name")
    fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
    warn "缺少依赖: ${MISSING[*]}"
    info "正在安装..."
    pip install --upgrade pip -q
    pip install "${MISSING[@]}" -q
    ok "依赖安装完成"
else
    ok "所有依赖已就绪"
fi

# 检查 Playwright Chromium
if ! python3 -c "
from playwright.sync_api import sync_playwright
try:
    with sync_playwright() as p:
        p.chromium.launch(headless=True)
    exit(0)
except Exception:
    exit(1)
" 2>/dev/null; then
    warn "Playwright Chromium 浏览器未安装，正在安装..."
    python3 -m playwright install chromium 2>&1 | tail -3
    ok "Playwright Chromium 安装完成"
else
    ok "Playwright Chromium 已就绪"
fi

# ---- 步骤 3: 清理旧构建 ----
info "步骤 3/5: 清理旧构建产物..."
rm -rf "$DIST_DIR" "$BUILD_DIR"
ok "已清理 dist/ 和 build/"

# ---- 步骤 4: 执行 py2app 打包 ----
info "步骤 4/5: 执行 py2app 打包（可能需要 1-3 分钟）..."

cd "$SCRIPT_DIR"

# 运行 py2app
if python3 setup.py py2app 2>&1 | tee /tmp/token_monitor_build.log; then
    ok "py2app 打包成功"
else
    err "py2app 打包失败，请查看上方日志"
    err "完整日志: /tmp/token_monitor_build.log"
    exit 1
fi

# 验证 .app 是否存在
if [ ! -d "$APP_BUNDLE" ]; then
    err ".app 捆绑包未生成: $APP_BUNDLE"
    exit 1
fi

# 显示 .app 大小
APP_SIZE=$(du -sh "$APP_BUNDLE" 2>/dev/null | cut -f1)
ok ".app 捆绑包已生成: $APP_BUNDLE ($APP_SIZE)"

# ---- 步骤 5: 后处理 ----
info "步骤 5/5: 后处理..."

# 验证 .app 结构完整性
if [ -f "$APP_BUNDLE/Contents/MacOS/$APP_NAME" ]; then
    ok "可执行文件存在: $APP_BUNDLE/Contents/MacOS/$APP_NAME"
else
    err "可执行文件缺失！打包可能不完整"
    exit 1
fi

if [ -f "$APP_BUNDLE/Contents/Info.plist" ]; then
    ok "Info.plist 存在"
else
    err "Info.plist 缺失！"
    exit 1
fi

# 检查 LSUIElement 是否正确设置
if plutil -p "$APP_BUNDLE/Contents/Info.plist" 2>/dev/null | grep -q "LSUIElement.*1"; then
    ok "LSUIElement = true（无 Dock 图标）"
else
    warn "LSUIElement 可能未正确设置"
fi

# ---- 可选: 安装到 /Applications ----
if [ "$INSTALL_FLAG" = true ]; then
    echo ""
    info "安装到 /Applications..."
    TARGET="/Applications/$APP_NAME.app"
    
    # 如果已有旧版本在运行，尝试退出
    if [ -d "$TARGET" ]; then
        warn "检测到已有安装: $TARGET"
        # 尝试优雅退出
        osascript -e "tell application \"$APP_NAME\" to quit" 2>/dev/null || true
        sleep 1
        rm -rf "$TARGET"
    fi
    
    cp -R "$APP_BUNDLE" "$TARGET"
    ok "已安装到: $TARGET"
    ok "用户可在「启动台」或「应用程序」中找到 Token Monitor"
fi

# ---- 可选: 生成 DMG ----
if [ "$DMG_FLAG" = true ]; then
    echo ""
    info "生成 DMG 安装镜像..."
    DMG_NAME="TokenMonitor-${VERSION}.dmg"
    DMG_PATH="$DIST_DIR/$DMG_NAME"
    DMG_TEMP="$DIST_DIR/dmg_temp"
    
    rm -rf "$DMG_TEMP" "$DMG_PATH"
    mkdir -p "$DMG_TEMP"
    cp -R "$APP_BUNDLE" "$DMG_TEMP/"
    # 创建 Applications 快捷方式
    ln -s /Applications "$DMG_TEMP/Applications"
    
    hdiutil create -volname "$APP_NAME" \
        -srcfolder "$DMG_TEMP" \
        -ov -format UDZO \
        "$DMG_PATH" 2>&1 | tail -3
    
    rm -rf "$DMG_TEMP"
    
    if [ -f "$DMG_PATH" ]; then
        DMG_SIZE=$(du -sh "$DMG_PATH" | cut -f1)
        ok "DMG 已生成: $DMG_PATH ($DMG_SIZE)"
    else
        err "DMG 生成失败"
    fi
fi

# ---- 完成 ----
echo ""
echo "=========================================="
echo "  ✅ 打包完成！"
echo "=========================================="
echo ""
echo "📦 产物位置:"
echo "   .app 捆绑包:  $APP_BUNDLE"
if [ "$DMG_FLAG" = true ]; then
    echo "   DMG 安装镜像: $DIST_DIR/TokenMonitor-${VERSION}.dmg"
fi
echo ""
echo "📌 使用方式:"
echo "   1. 双击 $APP_BUNDLE 即可运行"
echo "   2. 或拖拽到 /Applications 目录安装"
if [ "$INSTALL_FLAG" = true ]; then
    echo "   3. ✅ 已安装到 /Applications，可在启动台找到"
fi
echo ""
echo "💡 提示:"
echo "   - 首次运行会自动打开浏览器引导 OA 登录"
echo "   - 应用仅在状态栏显示图标，不会出现在 Dock"
echo "   - 如需卸载: 从 /Applications 删除 TokenMonitor.app 即可"
echo "   - 数据文件（日志/数据库/cookie）保存在 ~/Library/Application Support/TokenMonitor/"
echo ""
echo "=========================================="