#!/usr/bin/env python3
"""
Token Widget - 集中式 Token 仪表盘组件
========================================
为 Token Monitor 状态栏应用提供丰富的仪表盘菜单组件。
支持三种展示模式：
1. 精简模式（1x1）：Token 使用量和总量百分比
2. 标准模式：精简 + Top N 模型使用量排行
3. 完整模式：标准 + 其他有效统计（趋势、告警、刷新信息）

该组件作为 token_status_app.py 的菜单扩展，通过 rumps.MenuItem 嵌入。
"""

import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

logger = logging.getLogger("token_widget")

# ============================================================
# 仪表盘数据模型
# ============================================================

class TokenWidgetData:
    """仪表盘数据容器"""

    def __init__(self):
        self.used: int = 0
        self.total: int = 4060
        self.percent: float = 0.0
        self.last_update: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.consecutive_failures: int = 0
        self.model_stats: List[Dict[str, Any]] = []  # Top N 模型数据
        self.extra_stats: Dict[str, Any] = {}        # 其他统计

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.used)

    @property
    def remaining_percent(self) -> float:
        return max(0.0, 100.0 - self.percent)

    @property
    def status_emoji(self) -> str:
        if self.percent >= 80:
            return "🟥"
        elif self.percent >= 60:
            return "🟧"
        elif self.percent >= 40:
            return "🟨"
        else:
            return "🟩"

    @property
    def status_text(self) -> str:
        if self.percent >= 80:
            return "告警"
        elif self.percent >= 60:
            return "偏高"
        elif self.percent >= 40:
            return "中等"
        else:
            return "正常"


# ============================================================
# 仪表盘渲染器
# ============================================================

def build_progress_bar(percent: float, width: int = 12) -> str:
    """生成文本进度条，按消耗百分比着色。
    
    颜色规则：
    - < 40%:  绿色 🟩
    - 40~60%: 黄色 🟨
    - 60~80%: 橙色 🟧
    - ≥ 80%:  红色 🟥
    """
    filled = max(0, min(width, int(percent / 100 * width)))
    empty = width - filled
    
    # 根据百分比选择颜色字符
    if percent >= 80:
        fill_char = "🟥"  # 红色方块
    elif percent >= 60:
        fill_char = "🟧"  # 橙色方块
    elif percent >= 40:
        fill_char = "🟨"  # 黄色方块
    else:
        fill_char = "🟩"  # 绿色方块
    
    empty_char = "⬜"  # 白色空心方块
    
    bar = fill_char * filled + empty_char * empty
    return bar


def build_card_compact(data: TokenWidgetData) -> List[str]:
    """
    构建精简模式卡片（1x1）。
    显示：Token 使用量 / 总量 + 百分比 + 进度条 + 状态
    """
    lines = []
    lines.append(f"  {data.status_emoji} Token 使用情况")
    lines.append(f"  ─────────────────")
    lines.append(f"  已用: {data.used:,} / {data.total:,}")
    lines.append(f"  使用率: {data.percent:.1f}%  |  剩余: {data.remaining:.1f}%")
    lines.append(f"  [{build_progress_bar(data.percent)}]")
    lines.append(f"  状态: {data.status_text}")
    if data.last_update:
        lines.append(f"  更新: {data.last_update.strftime('%H:%M:%S')}")
    return lines


def build_card_model_stats(data: TokenWidgetData, top_n: int = 5) -> List[str]:
    """
    构建 Top N 模型使用量排行卡片。
    从 model_stats 中提取前 N 个模型的使用数据。
    """
    lines = []
    lines.append(f"  📊 Top {top_n} 模型使用量")
    lines.append(f"  ─────────────────")

    if not data.model_stats:
        lines.append(f"  (暂无模型数据)")
        return lines

    # 排序取前 N
    sorted_models = sorted(
        data.model_stats,
        key=lambda x: x.get("percent", 0) if x.get("percent") is not None else x.get("used", 0),
        reverse=True
    )[:top_n]

    for i, model in enumerate(sorted_models, 1):
        name = model.get("name", model.get("model", f"模型{i}"))
        used = model.get("used", model.get("tokens", 0))
        total = model.get("total", model.get("quota", 0))
        pct = model.get("percent", None)

        if pct is not None:
            bar = build_progress_bar(pct, width=8)
            lines.append(f"  {i}. {name[:20]:<20} {bar} {pct:.1f}%")
        elif total > 0:
            pct_calc = used / total * 100 if total > 0 else 0
            bar = build_progress_bar(pct_calc, width=8)
            lines.append(f"  {i}. {name[:20]:<20} {bar} {used}/{total}")
        else:
            # 判断是费用数据（元）还是 token 数量
            if model.get("is_fee"):
                lines.append(f"  {i}. {name[:20]:<20} ¥{used:,.2f}")
            else:
                lines.append(f"  {i}. {name[:20]:<20} {used:,} tokens")

    return lines


def build_card_extra_stats(data: TokenWidgetData) -> List[str]:
    """
    构建其他有效统计卡片。
    包括：刷新间隔、连续失败次数、数据库记录数、运行时长等。
    """
    lines = []
    lines.append(f"  📋 系统状态")
    lines.append(f"  ─────────────────")

    # 从 extra_stats 中读取
    stats = data.extra_stats

    if stats.get("refresh_interval"):
        lines.append(f"  刷新间隔: {stats['refresh_interval']}")
    if stats.get("uptime"):
        lines.append(f"  运行时长: {stats['uptime']}")
    if stats.get("db_records"):
        lines.append(f"  历史记录: {stats['db_records']} 条")
    if stats.get("last_error"):
        lines.append(f"  上次错误: {stats['last_error'][:40]}")
    if data.consecutive_failures > 0:
        lines.append(f"  连续失败: {data.consecutive_failures} 次 {'⚠️' if data.consecutive_failures >= 3 else ''}")

    # 如果没有额外数据，显示基本信息
    if not stats and data.last_update:
        now = datetime.now()
        delta = now - data.last_update
        if delta.total_seconds() < 3600:
            lines.append(f"  距上次更新: {int(delta.total_seconds())}秒前")
        else:
            lines.append(f"  距上次更新: {delta.total_seconds()/60:.0f}分钟前")

    return lines


def build_dashboard(data: TokenWidgetData, mode: str = "full", top_n: int = 5) -> List[str]:
    """
    构建完整仪表盘菜单项列表。

    参数:
        data: 仪表盘数据
        mode: "compact" | "standard" | "full"
        top_n: Top N 模型数量

    返回:
        rumps.MenuItem 列表（含 None 分隔线）
    """
    import rumps

    items = []

    # === 模式1: 精简模式（1x1）===
    compact_lines = build_card_compact(data)
    for line in compact_lines:
        items.append(rumps.MenuItem(line, callback=None))

    if mode == "compact":
        items.append(None)
        items.append(rumps.MenuItem("  🔄 点击刷新", callback=None))
        return items

    # === 模式2/3: 标准/完整模式 ===
    items.append(None)

    # Top N 模型
    model_lines = build_card_model_stats(data, top_n)
    for line in model_lines:
        items.append(rumps.MenuItem(line, callback=None))

    if mode == "standard":
        items.append(None)
        items.append(rumps.MenuItem("  🔄 点击刷新", callback=None))
        return items

    # === 模式3: 完整模式 ===
    items.append(None)

    # 其他统计
    extra_lines = build_card_extra_stats(data)
    for line in extra_lines:
        items.append(rumps.MenuItem(line, callback=None))

    items.append(None)
    items.append(rumps.MenuItem("  🔄 点击刷新", callback=None))

    return items


# ============================================================
# 仪表盘管理器 - 与 TokenStatusApp 集成
# ============================================================

class TokenWidgetManager:
    """
    仪表盘管理器，负责：
    - 维护仪表盘数据
    - 生成/更新菜单项
    - 管理三种展示模式的切换
    - 支持自动模式切换（根据使用率高低自动切换精简/完整模式）
    """

    # 自动模式切换阈值
    AUTO_MODE_THRESHOLDS = {
        "compact": (0, 30),       # 使用率 < 30% → 精简模式
        "standard": (30, 70),     # 30% ≤ 使用率 < 70% → 标准模式
        "full": (70, 101),        # 使用率 ≥ 70% → 完整模式
    }

    def __init__(self, app_instance=None):
        self.app = app_instance
        self.data = TokenWidgetData()
        self.mode = "full"  # compact | standard | full
        self.auto_mode = False  # 是否启用自动模式切换
        self.top_n = 5
        self._menu_item = None  # 仪表盘子菜单项引用

    def update_data(self, used: int, total: int, percent: float,
                    last_update: Optional[datetime] = None,
                    last_error: Optional[str] = None,
                    consecutive_failures: int = 0,
                    model_stats: Optional[List[Dict]] = None,
                    extra_stats: Optional[Dict] = None):
        """更新仪表盘数据"""
        self.data.used = used
        self.data.total = total
        self.data.percent = percent
        if last_update:
            self.data.last_update = last_update
        if last_error:
            self.data.last_error = last_error
        self.data.consecutive_failures = consecutive_failures
        if model_stats:
            self.data.model_stats = model_stats
        if extra_stats:
            self.data.extra_stats = extra_stats

        # 如果启用了自动模式，根据使用率自动切换
        if self.auto_mode:
            self._auto_switch_mode(percent)

    def _auto_switch_mode(self, percent: float):
        """根据使用率自动切换展示模式"""
        for mode, (low, high) in self.AUTO_MODE_THRESHOLDS.items():
            if low <= percent < high:
                if self.mode != mode:
                    old_mode = self.mode
                    self.mode = mode
                    logger.info(f"📊 自动切换模式: {old_mode} → {mode} (使用率 {percent:.1f}%)")
                break

    def set_mode(self, mode: str):
        """切换展示模式（手动设置时自动关闭自动模式）"""
        if mode in ("compact", "standard", "full"):
            self.mode = mode
            self.auto_mode = False  # 手动切换时关闭自动模式
            logger.info(f"📊 仪表盘模式已切换为: {mode}")

    def set_auto_mode(self, enabled: bool):
        """启用/关闭自动模式切换"""
        self.auto_mode = enabled
        if enabled:
            # 立即根据当前使用率切换
            self._auto_switch_mode(self.data.percent)
            logger.info(f"📊 自动模式已启用")
        else:
            logger.info(f"📊 自动模式已关闭")

    def build_menu_items(self) -> list:
        """生成仪表盘菜单项列表"""
        return build_dashboard(self.data, self.mode, self.top_n)

    def get_status_bar_text(self) -> str:
        """生成状态栏显示文本（简洁版）"""
        d = self.data
        bar = build_progress_bar(d.percent, width=6)
        return f"{d.status_emoji} {d.used}/{d.total} {bar} {d.percent:.0f}%"


# ============================================================
# 测试入口
# ============================================================
if __name__ == "__main__":
    # 测试数据
    data = TokenWidgetData()
    data.used = 862
    data.total = 4060
    data.percent = 21.2
    data.last_update = datetime.now()
    data.model_stats = [
        {"name": "GPT-4o", "used": 350, "total": 1000, "percent": 35.0},
        {"name": "Claude-3.5-Sonnet", "used": 280, "total": 1000, "percent": 28.0},
        {"name": "Gemini-Pro", "used": 120, "total": 800, "percent": 15.0},
        {"name": "DeepSeek-V3", "used": 62, "total": 500, "percent": 12.4},
        {"name": "Qwen-Max", "used": 50, "total": 760, "percent": 6.6},
    ]
    data.extra_stats = {
        "refresh_interval": "3 分钟",
        "uptime": "2小时15分",
        "db_records": 42,
    }

    print("=" * 50)
    print("📊 Token 仪表盘组件测试")
    print("=" * 50)

    print("\n【精简模式 1x1】")
    for line in build_card_compact(data):
        print(line)

    print("\n【标准模式 - Top 5 模型】")
    for line in build_card_model_stats(data, 5):
        print(line)

    print("\n【完整模式 - 系统状态】")
    for line in build_card_extra_stats(data):
        print(line)

    print("\n【状态栏文本】")
    mgr = TokenWidgetManager()
    mgr.update_data(862, 4060, 21.2, datetime.now())
    print(f"  {mgr.get_status_bar_text()}")

    print("\n✅ 仪表盘组件测试通过")