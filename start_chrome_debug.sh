#!/bin/bash
# 以远程调试模式启动 Chrome，供 token_monitor.py 的 --cdp 模式连接
# 用法: bash start_chrome_debug.sh

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT=9222

echo "🚀 正在以调试模式启动 Chrome（端口: $PORT）..."
echo "⚠️  请确保已关闭所有 Chrome 窗口"
echo ""
echo "Chrome 启动后，请登录 OA 系统（如需要）"
echo "然后在新终端中运行: python3 token_monitor.py --cdp"
echo ""

"$CHROME" --remote-debugging-port=$PORT --no-first-run --no-default-browser-check