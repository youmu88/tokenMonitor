#!/usr/bin/env python3
"""
Token Status Bar App - macOS 状态栏 Token 监控应用（增强版）
=============================================================
功能：
1. ✅ PyObjC 原生图标（NSImage 绘制进度条图标），替代纯文本状态栏
2. ✅ SQLite 数据持久化，记录历史趋势
3. ✅ 菜单中展示折线图（ASCII 图表）
4. ✅ 告警通知（使用率 > 80%/90%）
5. ✅ 可打包为 .app 捆绑包（py2app）
6. ✅ 可配置自动刷新间隔（1/3/5/30 分钟）
7. ✅ Cookie 过期自动检测与重新登录

首次运行：自动打开浏览器让用户登录 OA 系统，认证后开始监控。
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

import rumps

# 将项目根目录加入 path
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from token_monitor_core import TokenMonitorEngine, COOKIE_FILE, MAX_TOKEN, TARGET_URL, PROFILE_DIR
from token_db import init_db, record_token_data, get_recent_history, get_history_for_chart, cleanup_old_data
from token_icon import has_pyobjc, create_progress_icon, icon_to_rumps
from token_widget import TokenWidgetManager, build_progress_bar

# ============================================================
# 配置
# ============================================================
APP_NAME = "Token Monitor"
REFRESH_INTERVAL = 180  # 默认刷新间隔（秒），默认3分钟
# 可选的刷新间隔选项（秒）
REFRESH_OPTIONS = {
    "1 分钟": 60,
    "3 分钟": 180,
    "5 分钟": 300,
    "30 分钟": 1800,
}
LOG_FILE = os.path.join(APP_DIR, "token_monitor.log")
ALERT_THRESHOLD_HIGH = 90   # 高告警阈值
ALERT_THRESHOLD_MEDIUM = 80  # 中告警阈值
HISTORY_RETENTION_DAYS = 30  # 历史数据保留天数
# Cookie 缓存过期时间（秒），默认 7 天
# 当距离上次成功抓取超过此时间时，自动触发重新登录
COOKIE_EXPIRE_SECONDS = 7 * 24 * 3600  # 7 天
# 连续失败次数阈值，超过后触发重新登录
MAX_CONSECUTIVE_FAILURES = 3

# ============================================================
# 日志配置
# ============================================================
def setup_logging():
    """配置日志系统，同时输出到文件和控制台"""
    logger = logging.getLogger("token_status_app")
    logger.setLevel(logging.DEBUG)

    # 文件处理器 - 详细日志
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-5s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(fh)

    return logger

log = setup_logging()


class TokenStatusApp(rumps.App):
    """Token 监控状态栏应用（增强版）"""

    def __init__(self):
        # 初始化数据库
        init_db()
        # 清理旧数据
        cleanup_old_data(days=HISTORY_RETENTION_DAYS)

        # 初始状态
        self.used = 0
        self.total = MAX_TOKEN
        self.percent = 0.0
        self.last_error = None
        self.last_success_time = None
        self._alerted_medium = False
        self._alerted_high = False
        self._history_data = []
        self._consecutive_failures = 0  # 连续失败次数
        self._current_interval = REFRESH_INTERVAL  # 当前刷新间隔（秒）
        self._current_interval_label = "3 分钟"
# 仪表盘组件
        self.widget = TokenWidgetManager(app_instance=self)
        self.widget.set_mode("full")  # 默认完整模式  # 当前刷新间隔标签

        # 检测 PyObjC 是否可用
        self.use_native_icon = has_pyobjc()
        if self.use_native_icon:
            log.info("✅ PyObjC 可用，将使用原生图标")
        else:
            log.info("ℹ️ PyObjC 不可用，使用文本状态栏")

        # 初始化 rumps App
        # LSUIElement = True: 隐藏 Dock 图标，仅显示状态栏图标
        super().__init__(
            name="TokenMonitor",
            title="🔄 Tokens: ...",
            icon=None,
            quit_button=None,
        )

        self.engine = TokenMonitorEngine()
        self.monitoring = False

        # 构建菜单
        self._build_menu()

        # 定时器：使用当前设置的刷新间隔
        self.timer = rumps.Timer(self._timer_tick, self._current_interval)
        self.timer.start()

        log.info("=" * 60)
        log.info("🚀 Token Status App (增强版) 启动")
        log.info(f"日志文件: {LOG_FILE}")
        log.info(f"目标URL: {TARGET_URL}")
        log.info(f"总配额: {MAX_TOKEN}")
        log.info(f"刷新间隔: {self._current_interval}秒 ({self._current_interval_label})")
        log.info(f"Cookie 过期时间: {COOKIE_EXPIRE_SECONDS}秒 ({COOKIE_EXPIRE_SECONDS//86400}天)")
        log.info(f"PyObjC图标: {'启用' if self.use_native_icon else '未启用'}")
        log.info("=" * 60)

        # 检查 cookie
        if not self.engine.has_cookies():
            log.info("未检测到 Cookie，启动首次登录流程...")
            threading.Thread(target=self._first_time_setup, daemon=True).start()
        else:
            log.info("检测到已保存的 Cookie/Profile，开始监控...")
            threading.Thread(target=self._do_fetch, daemon=True).start()

    def _build_menu(self):
        """构建菜单"""
        # 构建历史趋势子菜单
        history_menu = rumps.MenuItem("📈 历史趋势", callback=None)
        history_menu.add(rumps.MenuItem("📊 最近 1 天 (24小时)", callback=self.show_history_1d))
        history_menu.add(rumps.MenuItem("📊 最近 7 天", callback=self.show_history_7d))
        history_menu.add(rumps.MenuItem("📊 最近 1 个月 (30天)", callback=self.show_history_30d))

        # 构建刷新间隔设置子菜单
        interval_menu = rumps.MenuItem("⏱ 刷新间隔", callback=None)
        for label, seconds in REFRESH_OPTIONS.items():
            checked = "✅ " if seconds == self._current_interval else "  "
            item = rumps.MenuItem(f"{checked}{label}", callback=self._set_interval)
            item._interval_seconds = seconds  # 存储间隔秒数
            item._interval_label = label
            interval_menu.add(item)

        # 仪表盘子菜单（动态更新）
        self._widget_menu = rumps.MenuItem("📊 Token 仪表盘", callback=None)
        self._widget_menu.add(rumps.MenuItem("  🟢 加载中...", callback=None))
        self._widget_menu.add(None)
        # 模式切换子菜单
        self._widget_mode_menu = rumps.MenuItem("🎨 切换模式", callback=None)
        self._widget_mode_menu.add(rumps.MenuItem("✅ 精简 (1x1)", callback=self._set_widget_mode_compact))
        self._widget_mode_menu.add(rumps.MenuItem("  标准 (+TopN)", callback=self._set_widget_mode_standard))
        self._widget_mode_menu.add(rumps.MenuItem("  完整", callback=self._set_widget_mode_full))
        self._widget_menu.add(self._widget_mode_menu)
        # 自动模式切换
        self._widget_menu.add(rumps.MenuItem("🤖 自动模式", callback=self._set_widget_auto_mode))

        self.menu = [
            rumps.MenuItem("📊 Tokens: 加载中...", callback=None),
            self._widget_menu,
            None,  # 分隔线
            rumps.MenuItem("🔄 立即刷新", callback=self.refresh_now),
            rumps.MenuItem("📋 查看日志", callback=self.open_log),
            rumps.MenuItem("🌐 打开网页", callback=self.open_website),
            None,
            history_menu,
            interval_menu,
            None,
            rumps.MenuItem("🔑 重新登录", callback=self.re_login),
            rumps.MenuItem("❌ 退出", callback=self.quit_app),
        ]

    def _set_interval(self, sender):
        """设置刷新间隔"""
        seconds = getattr(sender, '_interval_seconds', REFRESH_INTERVAL)
        label = getattr(sender, '_interval_label', "3 分钟")
        self._current_interval = seconds
        self._current_interval_label = label

        # 重启定时器
        if hasattr(self, 'timer') and self.timer:
            self.timer.stop()
        self.timer = rumps.Timer(self._timer_tick, self._current_interval)
        self.timer.start()

        log.info(f"⏱ 刷新间隔已设置为: {label} ({seconds}秒)")

        # 更新菜单项的勾选状态
        interval_menu = self.menu.get("⏱ 刷新间隔")
        if interval_menu:
            for item in interval_menu:
                item_label = getattr(item, '_interval_label', None)
                if item_label:
                    checked = "✅ " if item_label == label else "  "
                    item.title = f"{checked}{item_label}"

        rumps.notification(
            title="Token 监控",
            subtitle=f"刷新间隔已设置为 {label}",
            message=f"每 {label} 自动抓取一次 Token 数据",
        )

    def _first_time_setup(self):
        """首次使用：启动登录流程"""
        log.info("首次使用，启动登录流程...")
        rumps.notification(
            title="Token 监控",
            subtitle="首次使用，请完成 OA 登录认证",
            message="浏览器已打开，请在页面中登录 OA 系统",
        )
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            success = loop.run_until_complete(self.engine.run_login_flow())
            if success:
                log.info("✅ 登录成功，开始监控")
                self._consecutive_failures = 0  # 重置连续失败计数
                rumps.notification(
                    title="Token 监控",
                    subtitle="✅ 登录成功！",
                    message="开始监控 Token 使用情况",
                )
                loop.run_until_complete(self._fetch_and_update())
            else:
                log.error("❌ 登录失败或超时")
                rumps.notification(
                    title="Token 监控",
                    subtitle="❌ 登录失败",
                    message="请重新启动应用并重试",
                )
        finally:
            loop.close()

    def _do_fetch(self):
        """在新线程中执行抓取"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._fetch_and_update())
        finally:
            loop.close()

    async def _fetch_and_update(self):
        """异步抓取并更新状态"""
        log.info("开始抓取 Token 数据...")
        result = await self.engine.fetch_token_data()

        if result["success"]:
            used = result.get("used") or 0
            total = result.get("total") or MAX_TOKEN
            percent = result.get("percent")
            # BUGFIX: 当 percent 为 None 时（页面解析失败），使用 0.0 作为默认值
            if percent is None:
                percent = 0.0
            self.used = used
            self.total = total
            self.percent = percent
            self.last_error = None
            self.last_success_time = datetime.now()
            self._consecutive_failures = 0  # 重置连续失败计数

            # 提取模型统计数据（如果有）
            model_stats = result.get("model_stats", [])
            if model_stats:
                self._model_stats = model_stats
                log.info(f"📊 提取到 {len(model_stats)} 个模型使用量数据")
                # 同步更新 widget 的模型数据
                self.widget.data.model_stats = model_stats

            # 记录到数据库
            record_token_data(used, total, percent, success=True)

            # 检查告警阈值
            self._check_alerts(percent)

            log.info(f"✅ 抓取成功 | 已用: {used}/{total} ({percent:.1f}%)")
        else:
            self.last_error = result.get("error", "未知错误")
            self._consecutive_failures += 1
            log.warning(f"⚠️ 抓取失败: {self.last_error} (连续失败: {self._consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})")

            # 记录失败到数据库（使用上次成功的数据）
            record_token_data(self.used, self.total, self.percent, success=False, error=self.last_error)

            # 检查是否需要自动重新登录
            if self._should_re_login():
                log.warning("⚠️ 连续失败次数过多或 Cookie 已过期，触发自动重新登录...")
                rumps.notification(
                    title="Token 监控",
                    subtitle="⚠️ 登录状态已失效",
                    message="将自动打开浏览器进行重新登录...",
                )
                threading.Thread(target=self._auto_re_login, daemon=True).start()

        # 更新显示
        self._refresh_display()

    def _should_re_login(self) -> bool:
        """判断是否需要自动重新登录"""
        # 条件1：连续失败次数超过阈值
        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            log.info(f"触发重新登录：连续失败 {self._consecutive_failures} 次 >= {MAX_CONSECUTIVE_FAILURES}")
            return True

        # 条件2：上次成功时间距今超过 Cookie 过期时间
        if self.last_success_time is not None:
            elapsed = (datetime.now() - self.last_success_time).total_seconds()
            if elapsed >= COOKIE_EXPIRE_SECONDS:
                log.info(f"触发重新登录：上次成功抓取距今 {elapsed:.0f}秒 >= {COOKIE_EXPIRE_SECONDS}秒")
                return True

        return False

    def _auto_re_login(self):
        """自动重新登录"""
        log.info("🔑 开始自动重新登录流程...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # 先清理旧的 cookie 和 profile
            if os.path.exists(COOKIE_FILE):
                os.remove(COOKIE_FILE)
                log.info("已删除旧 Cookie 文件")
            # 清理 profile 目录（可能已损坏）
            if os.path.isdir(PROFILE_DIR):
                import shutil
                shutil.rmtree(PROFILE_DIR, ignore_errors=True)
                log.info("已清理旧的 Profile 目录")

            success = loop.run_until_complete(self.engine.run_login_flow())
            if success:
                log.info("✅ 自动重新登录成功")
                self._consecutive_failures = 0
                rumps.notification(
                    title="Token 监控",
                    subtitle="✅ 自动重新登录成功！",
                    message="已恢复 Token 数据监控",
                )
                loop.run_until_complete(self._fetch_and_update())
            else:
                log.error("❌ 自动重新登录失败")
                rumps.notification(
                    title="Token 监控",
                    subtitle="❌ 自动重新登录失败",
                    message="请手动点击「重新登录」按钮重试",
                )
        finally:
            loop.close()

    def _refresh_display(self):
        """更新状态栏显示和菜单"""
        if self.last_error and not self.last_success_time:
            # 从未成功过
            self._update_title("⚠️", "错误", self.last_error)
            self._set_menu_title(0, f"⚠️ Tokens: {self.last_error}")
            return

        used = self.used
        total = self.total
        percent = self.percent

        # 1. 更新状态栏图标/文本
        if self.use_native_icon:
            # 使用 PyObjC 原生图标
            ns_image = create_progress_icon(percent, width=22, height=22)
            if ns_image:
                rumps_icon = icon_to_rumps(ns_image)
                if rumps_icon:
                    self.icon = rumps_icon

        # 状态栏标题（同时保留文本作为 fallback）
        # 按消耗百分比着色：<40%绿 / 40~60%黄 / 60~80%橙 / 80%+红
        if percent >= 80:
            bar_fill = "🟥"
        elif percent >= 60:
            bar_fill = "🟧"
        elif percent >= 40:
            bar_fill = "🟨"
        else:
            bar_fill = "🟩"
        bar_empty = "⬜"
        filled_count = max(0, min(8, int(percent / 100 * 8)))
        empty_count = 8 - filled_count
        bar_chars = bar_fill * filled_count + bar_empty * empty_count
        self.title = f"Tokens: {used}/{total}({percent:.0f}%)"

        # 2. 更新菜单项
        status_icon = "✅" if percent < 80 else ("⚠️" if percent < 90 else "🔴")
        self._set_menu_title(0, f"{status_icon} Tokens: {used}/{total} ({percent:.1f}%) {bar_chars}")

        # 3. 更新历史数据缓存
        self._history_data = get_history_for_chart(hours=24)
# 4. 刷新仪表盘
        self._refresh_widget_display()

    def _set_menu_title(self, index, title):
        """安全地设置菜单项标题（兼容 rumps 的 menu 对象）"""
        try:
            # rumps 的 menu 支持整数索引访问
            self.menu[index].title = title
        except (KeyError, IndexError, TypeError, AttributeError):
            # fallback: 通过遍历 menu.items() 来设置
            try:
                items = list(self.menu.values())
                if 0 <= index < len(items):
                    items[index].title = title
            except Exception as e:
                log.debug(f"无法更新菜单项 [{index}] 标题: {e}")

    def _check_alerts(self, percent: float):
        """检查是否需要发送告警通知"""
        # BUGFIX: 防御 percent 为 None 的情况
        if percent is None:
            return
        if percent >= ALERT_THRESHOLD_HIGH and not self._alerted_high:
            self._alerted_high = True
            self._alerted_medium = True  # 高告警覆盖中告警
            log.warning(f"🔴 高告警: Token 使用率 {percent:.1f}% >= {ALERT_THRESHOLD_HIGH}%")
            rumps.notification(
                title="🔴 Token 使用率告警",
                subtitle=f"使用率已达 {percent:.1f}%",
                message=f"已用 {self.used}/{self.total}，请及时关注！",
            )
        elif percent >= ALERT_THRESHOLD_MEDIUM and not self._alerted_medium:
            self._alerted_medium = True
            log.warning(f"⚠️ 中告警: Token 使用率 {percent:.1f}% >= {ALERT_THRESHOLD_MEDIUM}%")
            rumps.notification(
                title="⚠️ Token 使用率提醒",
                subtitle=f"使用率已达 {percent:.1f}%",
                message=f"已用 {self.used}/{self.total}",
            )
        elif percent < ALERT_THRESHOLD_MEDIUM:
            # 使用率下降后重置告警状态
            self._alerted_medium = False
            self._alerted_high = False

    def _update_title(self, icon: str, label: str, detail: str = ""):
        """更新状态栏标题"""
        self.title = f"{icon} Tokens: {label}"
        if detail:
            self.title += f" ({detail})"

    def _timer_tick(self, sender):
        """定时器触发（保留给未来扩展）"""
        pass

    # ============================================================
    # 仪表盘模式切换
    # ============================================================

    def _set_widget_mode_compact(self, sender):
        """切换为精简模式（1x1）"""
        self.widget.set_mode("compact")
        self._update_widget_mode_menu()
        self._refresh_widget_display()
        log.info("📊 仪表盘模式切换为: 精简 (1x1)")

    def _set_widget_mode_standard(self, sender):
        """切换为标准模式（+TopN）"""
        self.widget.set_mode("standard")
        self._update_widget_mode_menu()
        self._refresh_widget_display()
        log.info("📊 仪表盘模式切换为: 标准 (+TopN)")

    def _set_widget_mode_full(self, sender):
        """切换为完整模式"""
        self.widget.set_mode("full")
        self._update_widget_mode_menu()
        self._refresh_widget_display()
        log.info("📊 仪表盘模式切换为: 完整")

    def _set_widget_auto_mode(self, sender):
        """切换自动模式"""
        self.widget.set_auto_mode(not self.widget.auto_mode)
        self._update_widget_mode_menu()
        self._refresh_widget_display()
        mode_label = "启用" if self.widget.auto_mode else "关闭"
        log.info(f"📊 自动模式已{mode_label}")
        rumps.notification(
            title="Token 监控",
            subtitle=f"📊 自动模式已{mode_label}",
            message=f"当前使用率: {self.percent:.1f}% → 模式: {self.widget.mode}",
        )

    def _update_widget_mode_menu(self):
        """更新模式切换菜单的勾选状态"""
        mode = self.widget.mode
        auto = self.widget.auto_mode
        labels = {
            "compact": "精简 (1x1)",
            "standard": "标准 (+TopN)",
            "full": "完整",
        }
        for item in self._widget_mode_menu:
            for mode_name, label in labels.items():
                if label in item.title:
                    checked = "✅ " if mode == mode_name else "  "
                    item.title = f"{checked}{label}"
                    break

        # 更新自动模式菜单项
        auto_item = self._widget_menu.get("🤖 自动模式")
        if auto_item:
            auto_item.title = "✅ 🤖 自动模式" if auto else "🤖 自动模式"

    def _refresh_widget_display(self):
        """刷新仪表盘菜单显示"""
        try:
            # 获取当前数据
            used = self.used
            total = self.total
            percent = self.percent
            last_update = self.last_success_time

            # 更新仪表盘数据
            self.widget.update_data(used, total, percent, last_update)

            # 如果有模型统计数据，也更新
            if hasattr(self, '_model_stats') and self._model_stats:
                self.widget.data.model_stats = self._model_stats

            # 重建仪表盘菜单
            menu_items = self.widget.build_menu_items()
            self._widget_menu.clear()
            for item in menu_items:
                self._widget_menu.add(item)

        except Exception as e:
            log.error(f"❌ 刷新仪表盘失败: {e}")

    # ============================================================
    # 菜单回调
    # ============================================================

    @rumps.clicked("🔄 立即刷新")
    def refresh_now(self, sender):
        """手动刷新"""
        log.info("🔄 用户手动触发刷新")
        self._set_menu_title(0, "📊 刷新中...")
        threading.Thread(target=self._do_fetch, daemon=True).start()

    @rumps.clicked("📋 查看日志")
    def open_log(self, sender):
        """打开日志文件"""
        if os.path.exists(LOG_FILE):
            os.system(f'open "{LOG_FILE}"')
            log.info(f"已打开日志文件: {LOG_FILE}")
        else:
            rumps.notification(
                title="Token 监控",
                subtitle="日志文件不存在",
                message="还没有日志记录",
            )

    @rumps.clicked("🌐 打开网页")
    def open_website(self, sender):
        """在浏览器中打开 token 监控网页"""
        webbrowser.open(TARGET_URL)
        log.info(f"已打开网页: {TARGET_URL}")

    def show_history_1d(self, sender):
        """显示最近 1 天（24小时）历史趋势"""
        self._show_history(hours=24, label="1 天")

    def show_history_7d(self, sender):
        """显示最近 7 天历史趋势"""
        self._show_history(hours=168, label="7 天")

    def show_history_30d(self, sender):
        """显示最近 1 个月（30天）历史趋势"""
        self._show_history(hours=720, label="1 个月")

    def _show_history(self, hours: int, label: str):
        """显示历史趋势折线图（按小时聚合）"""
        log.info(f"📈 用户查看历史趋势 ({label})")
        # 获取原始记录列表
        records = get_recent_history(hours=hours)
        if not records:
            rumps.notification(
                title="Token 监控",
                subtitle="暂无历史数据",
                message="请等待几次自动抓取后再查看",
            )
            return

        # 按小时聚合数据（取每小时最后一条记录）
        hourly_data = self._aggregate_by_hour(records)

        # 生成 ASCII 折线图
        chart = self._generate_ascii_chart(hourly_data)
        log.info(f"📈 历史趋势图表生成完成 ({label})，共 {len(hourly_data)} 个数据点")

        # 显示在通知中
        rumps.notification(
            title=f"📈 历史趋势 (最近{label})",
            subtitle=f"当前: {self.used}/{self.total} ({self.percent:.1f}%)",
            message=chart[:80],
        )

        # 用子菜单展开显示完整图表
        self._update_history_submenu(hourly_data, chart, label)

    def _aggregate_by_hour(self, records: list) -> list:
        """按小时聚合数据，取每小时最后一条记录"""
        if not records:
            return []

        hour_groups = {}  # key: "YYYY-MM-DD HH:00"
        for r in records:
            try:
                dt = datetime.fromisoformat(r["timestamp"])
                hour_key = dt.strftime("%Y-%m-%d %H:00")
                hour_groups[hour_key] = r  # 后面的覆盖前面的，最终保留每小时最后一条
            except Exception:
                continue

        # 按时间排序
        sorted_hours = sorted(hour_groups.keys())
        return [hour_groups[k] for k in sorted_hours]

    def _generate_ascii_chart(self, records: list) -> str:
        """生成 ASCII 折线图"""
        if not records:
            return "暂无数据"

        values = [r["percent"] for r in records]
        timestamps = [r["timestamp"] for r in records]

        if not values:
            return "暂无数据"

        min_val = min(values)
        max_val = max(values)
        current_val = values[-1]

        # 图表高度
        height = 10
        chart_width = len(values)

        # 构建图表
        lines = []
        for row in range(height, 0, -1):
            threshold = min_val + (max_val - min_val) * row / height if max_val > min_val else 50
            line = ""
            for v in values:
                if v >= threshold:
                    line += "█"
                elif v >= threshold - (max_val - min_val) / height / 2:
                    line += "▄"
                else:
                    line += " "
            lines.append(line)

        # 添加 Y 轴标签
        labeled_lines = []
        for i, line in enumerate(lines):
            pct = min_val + (max_val - min_val) * (height - i) / height if max_val > min_val else 50
            labeled_lines.append(f"{pct:5.0f}% | {line}")

        # 底部时间轴
        time_axis = "       "
        if len(timestamps) > 1:
            step = max(1, len(timestamps) // 6)
            for i, ts in enumerate(timestamps):
                try:
                    dt = datetime.fromisoformat(ts)
                    label = dt.strftime("%H:%M")
                except Exception:
                    label = ts[-5:] if len(ts) >= 5 else ts
                if i % step == 0 or i == len(timestamps) - 1:
                    time_axis += label
                    time_axis += " " * (step - 1) if step > 1 else ""
                else:
                    time_axis += " "

        chart_str = "\n".join(labeled_lines)
        chart_str += f"\n       {'─' * chart_width}"
        chart_str += f"\n{time_axis}"
        chart_str += f"\n\n📊 范围: {min_val:.0f}% ~ {max_val:.0f}% | 当前: {current_val:.1f}%"

        return chart_str

    def _update_history_submenu(self, hourly_data: list, chart: str, label: str = "1 天"):
        """更新历史趋势子菜单，显示完整图表和逐小时数据"""
        try:
            # 通过标题直接访问历史趋势菜单项
            history_item = self.menu.get("📈 历史趋势")
            if history_item is None:
                log.warning("⚠️ 未找到历史趋势菜单项")
                return

            # 确保 history_item 的 _menu 已初始化（MenuItem 初始化时 _menu 可能为 None）
            if history_item._menu is None:
                log.warning("⚠️ 历史趋势菜单项 _menu 未初始化，跳过更新")
                return

            # 移除旧的图表内容（从第4项开始，即三个选项之后的内容）
            # 保留前3项（三个时间范围选项）
            # 使用 keys() 获取所有键，避免在迭代过程中修改字典
            keys = list(history_item.keys())
            while len(keys) > 3:
                key = keys.pop()
                try:
                    del history_item[key]
                except Exception:
                    pass

            # 图表标题
            history_item.add(rumps.MenuItem(f"📊 {label}趋势图", callback=None))
            history_item.add(None)

            # 图表每一行作为一个菜单项
            for line in chart.split("\n"):
                if line.strip():
                    history_item.add(rumps.MenuItem(f"  {line}", callback=None))

            history_item.add(None)
            history_item.add(rumps.MenuItem("📋 逐小时明细:", callback=None))
            history_item.add(None)

            # 逐小时数据（根据时间范围动态显示数量）
            max_items = 12 if "天" in label else 24
            display_count = min(len(hourly_data), max_items)
            for r in hourly_data[-display_count:]:
                try:
                    dt = datetime.fromisoformat(r["timestamp"])
                    ts = dt.strftime("%m-%d %H:00") if "个月" in label or "7 天" in label else dt.strftime("%H:00")
                except Exception:
                    ts = r["timestamp"][-8:]
                history_item.add(rumps.MenuItem(
                    f"  {ts}  {r['used']}/{r['total']} ({r['percent']:.1f}%)",
                    callback=None,
                ))

            log.info(f"✅ 历史趋势子菜单已更新 ({label})，共显示 {display_count} 个数据点")
        except Exception as e:
            log.error(f"❌ 更新历史趋势子菜单失败: {e}")

    @rumps.clicked("🔑 重新登录")
    def re_login(self, sender):
        """重新登录"""
        log.info("🔑 用户请求重新登录")
        if os.path.exists(COOKIE_FILE):
            os.remove(COOKIE_FILE)
            log.info("已删除旧 Cookie 文件")
        rumps.notification(
            title="Token 监控",
            subtitle="🔑 重新登录",
            message="浏览器已打开，请重新登录 OA 系统",
        )
        threading.Thread(target=self._first_time_setup, daemon=True).start()

    @rumps.clicked("❌ 退出")
    def quit_app(self, sender):
        """退出应用 - 彻底终止进程，防止 macOS 自动重启"""
        log.info("👋 用户退出应用")
        log.info("=" * 60)
        rumps.notification(
            title="Token 监控",
            subtitle="已退出",
            message="Token 监控已停止",
        )
        # 先停止定时器
        if hasattr(self, 'timer') and self.timer:
            self.timer.stop()
        # 卸载 launchd 服务，防止 KeepAlive 自动重启
        try:
            plist_path = os.path.expanduser(
                "~/Library/LaunchAgents/com.token.monitor.plist"
            )
            if os.path.exists(plist_path):
                subprocess.run(
                    ["launchctl", "unload", plist_path],
                    capture_output=True, timeout=5,
                )
                log.info("✅ 已卸载 launchd 服务，防止自动重启")
        except Exception as e:
            log.warning(f"⚠️ 卸载 launchd 服务失败: {e}")
        # 使用 os._exit(0) 确保进程彻底退出
        import os
        os._exit(0)


def main():
    """启动状态栏应用"""
    log.info("正在初始化 Token Status App (增强版)...")

    # 隐藏 Dock 图标，仅保留状态栏图标
    try:
        from Cocoa import NSApplication, NSApplicationActivationPolicyAccessory
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        log.info("✅ 已设置 LSUIElement 模式（隐藏 Dock 图标）")
    except ImportError:
        log.warning("⚠️ 无法导入 Cocoa，Dock 图标可能仍然显示")

    app = TokenStatusApp()
    log.info("✅ Token Status App (增强版) 已启动，显示在状态栏")
    app.run()


if __name__ == "__main__":
    main()