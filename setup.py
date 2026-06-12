"""
Token Monitor - macOS 状态栏应用打包脚本
========================================
使用 py2app 打包为 .app 捆绑包，用户双击即可运行。

用法:
    python3 setup.py py2app

打包前确保已安装依赖:
    pip install py2app rumps pyobjc-core pyobjc-framework-Cocoa playwright
"""

import sys
import os
from pathlib import Path
from setuptools import setup

APP_NAME = "TokenMonitor"
APP_SCRIPT = "token_status_app.py"

# 从 VERSION 文件读取版本号（统一版本管理）
_VERSION_FILE = Path(__file__).resolve().parent / "VERSION"
if _VERSION_FILE.exists():
    APP_VERSION = _VERSION_FILE.read_text().strip()
else:
    APP_VERSION = "1.2.1"

# 应用资源文件（需要包含在 .app/Contents/Resources 中）
RESOURCE_FILES = [
    "token_monitor_core.py",
    "token_db.py",
    "token_icon.py",
]

# 查找 playwright 的 driver 目录，将其作为资源打包
# playwright 需要访问 driver/node 可执行文件和 driver/package/cli.js
# 如果放在 python314.zip 中，node 无法从 zip 加载模块
import playwright as _playwright
_PLAYWRIGHT_DIR = os.path.dirname(os.path.abspath(_playwright.__file__))
_PLAYWRIGHT_DRIVER_DIR = os.path.join(_PLAYWRIGHT_DIR, "driver")
if os.path.isdir(_PLAYWRIGHT_DRIVER_DIR):
    RESOURCE_FILES.append(_PLAYWRIGHT_DRIVER_DIR)

# py2app 选项
PLIST = {
    "CFBundleName": APP_NAME,
    "CFBundleDisplayName": "Token Monitor",
    "CFBundleIdentifier": "com.tokenmonitor.app",
    "CFBundleVersion": APP_VERSION,
    "CFBundleShortVersionString": APP_VERSION,
    "CFBundleExecutable": APP_NAME,
    "LSUIElement": True,       # 无 Dock 图标（纯状态栏应用）
    "NSHighResolutionCapable": True,
    "NSHumanReadableCopyright": "Copyright 2026 Token Monitor",
    # 防止 macOS 自动重启应用（退出后不重新打开）
    "NSSupportsAutomaticTermination": False,
    "NSQuitAlwaysKeepsWindows": False,
    "LSUIPresentationMode": 4,  # 4 = 不显示任何 UI（纯状态栏）
}

OPTIONS = {
    "argv_emulation": False,
    "packages": [
        "rumps",
        "token_monitor_core",
        "token_db",
        "token_icon",
    ],
    "includes": [
        "AppKit",
        "Quartz",
        "Foundation",
        "PyObjCTools",
        "Cocoa",
        "playwright",
        "playwright.sync_api",
    ],
    "excludes": [
        "tkinter",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "IPython",
        "jupyter",
        "PIL",
        "Pillow",
        "cv2",
        "curses",
        "email",
        "http",
        "html",
        "unittest",
        "test",
        "pydoc",
        "distutils",
        "ensurepip",
    ],
    "plist": PLIST,
    "resources": RESOURCE_FILES,
    "site_packages": True,
    "frameworks": [],
    # 保留 .py 源码以便运行时 import（token_monitor_core 等作为模块被 import）
    "strip": False,
}

setup(
    name=APP_NAME,
    version=APP_VERSION,
    description="Token 使用情况 macOS 状态栏监控应用",
    author="Token Monitor",
    app=[APP_SCRIPT],
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)