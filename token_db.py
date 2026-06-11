#!/usr/bin/env python3
"""
Token Database - SQLite 数据持久化模块
======================================
记录 Token 使用历史趋势，支持查询和图表数据生成。
"""

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("token_db")

# 数据库文件路径
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(APP_DIR, "token_history.db")

# 线程本地存储（SQLite 连接不可跨线程共享）
_local = threading.local()


def _get_connection() -> sqlite3.Connection:
    """获取当前线程的数据库连接"""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_FILE)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA synchronous=NORMAL")
    return _local.conn


def init_db():
    """初始化数据库表结构"""
    conn = _get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS token_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            used INTEGER NOT NULL,
            total INTEGER NOT NULL,
            percent REAL NOT NULL,
            success INTEGER NOT NULL DEFAULT 1,
            error TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_token_history_ts
        ON token_history(timestamp DESC)
    """)
    conn.commit()
    logger.info(f"✅ 数据库已初始化: {DB_FILE}")


def record_token_data(used: int, total: int, percent: float, success: bool = True, error: Optional[str] = None):
    """记录一条 Token 使用数据"""
    try:
        # BUGFIX: 防御 None 值
        if used is None:
            used = 0
        if total is None:
            total = 4060
        if percent is None:
            percent = 0.0

        conn = _get_connection()
        conn.execute(
            "INSERT INTO token_history (timestamp, used, total, percent, success, error) VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.now().isoformat(), used, total, round(percent, 1), 1 if success else 0, error),
        )
        conn.commit()
        logger.debug(f"📝 已记录: used={used}, total={total}, percent={percent:.1f}%")
    except Exception as e:
        logger.error(f"❌ 记录数据失败: {e}")


def get_recent_history(hours: int = 24) -> list[dict]:
    """获取最近 N 小时的历史记录"""
    conn = _get_connection()
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        "SELECT * FROM token_history WHERE timestamp >= ? ORDER BY timestamp ASC",
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_history_for_chart(hours: int = 24, max_points: int = 60) -> dict:
    """
    获取用于折线图展示的历史数据。
    返回: { labels: ["08:00", "08:01", ...], values: [45.2, 46.1, ...], used_values: [...], total: 4060 }
    """
    records = get_recent_history(hours)

    # 如果记录太多，降采样
    if len(records) > max_points:
        step = len(records) / max_points
        sampled = []
        for i in range(max_points):
            idx = min(int(i * step), len(records) - 1)
            sampled.append(records[idx])
        records = sampled

    labels = []
    values = []
    used_values = []
    total = 4060

    for r in records:
        try:
            dt = datetime.fromisoformat(r["timestamp"])
            label = dt.strftime("%H:%M")
        except Exception:
            label = r["timestamp"][-8:] if len(r["timestamp"]) >= 8 else r["timestamp"]

        labels.append(label)
        values.append(r["percent"])
        used_values.append(r["used"])
        total = r["total"]

    return {
        "labels": labels,
        "values": values,
        "used_values": used_values,
        "total": total,
    }


def render_ascii_chart(hours: int = 24, width: int = 60, height: int = 10) -> str:
    """
    生成 ASCII 折线图字符串。
    返回: 多行字符串，可直接打印或显示在菜单中。
    """
    data = get_history_for_chart(hours)
    labels = data["labels"]
    values = data["values"]
    total = data["total"]

    if not values:
        return "暂无数据"

    min_val = min(values)
    max_val = max(values)
    current_val = values[-1] if values else 0

    # 图表高度
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
    if len(labels) > 1:
        step = max(1, len(labels) // 6)
        for i, label in enumerate(labels):
            if i % step == 0 or i == len(labels) - 1:
                time_axis += label[-5:]  # 只显示 HH:MM
                time_axis += " " * (step - 1) if step > 1 else ""
            else:
                time_axis += " "

    chart_str = "\n".join(labeled_lines)
    chart_str += f"\n       {'─' * min(width, len(values))}"
    chart_str += f"\n{time_axis}"
    chart_str += f"\n\n📊 范围: {min_val:.0f}% ~ {max_val:.0f}% | 当前: {current_val:.1f}% | 总配额: {total}"

    return chart_str


def get_chart_menu_items(hours: int = 24) -> list:
    """
    获取用于 rumps 菜单的图表项列表。
    返回: [rumps.MenuItem, ...]
    """
    import rumps

    data = get_history_for_chart(hours)
    labels = data["labels"]
    values = data["values"]
    total = data["total"]

    if not values:
        return [rumps.MenuItem("  (暂无数据)", callback=None)]

    items = []
    items.append(rumps.MenuItem(f"  📊 最近 {hours}h 趋势:", callback=None))
    items.append(rumps.MenuItem(f"  总配额: {total} | 当前: {values[-1]:.1f}%", callback=None))
    items.append(rumps.MenuItem(f"  最高: {max(values):.1f}% | 最低: {min(values):.1f}%", callback=None))
    items.append(None)  # 分隔线

    # 最近 10 条记录
    records = get_recent_history(hours)
    recent = records[-10:] if len(records) > 10 else records
    for r in recent:
        try:
            dt = datetime.fromisoformat(r["timestamp"])
            ts = dt.strftime("%H:%M")
        except Exception:
            ts = r["timestamp"][-8:]
        items.append(rumps.MenuItem(
            f"  {ts}  {r['used']}/{r['total']} ({r['percent']:.1f}%)",
            callback=None,
        ))

    return items


def cleanup_old_data(days: int = 30):
    """清理超过指定天数的旧数据"""
    try:
        conn = _get_connection()
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        deleted = conn.execute(
            "DELETE FROM token_history WHERE timestamp < ?",
            (cutoff,),
        ).rowcount
        conn.commit()
        if deleted > 0:
            logger.info(f"🧹 已清理 {deleted} 条超过 {days} 天的旧数据")
    except Exception as e:
        logger.error(f"❌ 清理旧数据失败: {e}")


# ============================================================
# 初始化
# ============================================================
init_db()