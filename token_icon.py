#!/usr/bin/env python3
"""
Token Icon - PyObjC 原生图标模块
==================================
使用 NSImage 自定义绘制进度条图标，替代纯文本状态栏。
支持根据 Token 使用率动态渲染不同颜色的进度条图标。
"""

import logging
import math
import os
from typing import Optional, Tuple

import rumps

logger = logging.getLogger("token_icon")

# ============================================================
# PyObjC 原生图标绘制
# ============================================================
def _import_pyobjc():
    """延迟导入 PyObjC，避免无 GUI 环境报错"""
    try:
        from AppKit import NSImage, NSBezierPath, NSColor, NSFont, NSFontAttributeName, \
            NSForegroundColorAttributeName, NSMakeRect, NSMakePoint, NSMakeSize, \
            NSStringDrawing, NSCompositingOperationSourceOver, NSGraphicsContext, \
            NSAttributedString, NSBaselineOffsetAttributeName
        from Quartz import CGContextSetShouldAntialias, CGContextSetAllowsAntialiasing, \
            kCGImageAlphaPremultipliedFirst, CGBitmapContextCreate, CGColorSpaceCreateDeviceRGB
        return True
    except ImportError:
        return False


_HAS_PYOBJC = _import_pyobjc()


def has_pyobjc() -> bool:
    """检查 PyObjC 是否可用"""
    return _HAS_PYOBJC


def create_progress_icon(percent: float, width: int = 22, height: int = 22) -> Optional[object]:
    """
    使用 NSImage 绘制进度条图标。
    
    参数:
        percent: 使用率 (0~100)
        width: 图标宽度
        height: 图标高度
    
    返回:
        NSImage 对象，或 None（PyObjC 不可用时）
    """
    if not _HAS_PYOBJC:
        return None

    from AppKit import NSImage, NSBezierPath, NSColor, NSMakeRect, NSMakeSize, \
        NSGraphicsContext, NSCompositingOperationSourceOver

    # 创建 NSImage
    size = NSMakeSize(width, height)
    image = NSImage.alloc().initWithSize_(size)
    
    image.lockFocus()
    
    # 获取当前图形上下文
    context = NSGraphicsContext.currentContext()
    
    # 绘制背景圆角矩形
    bg_rect = NSMakeRect(1, 1, width - 2, height - 2)
    bg_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bg_rect, 4, 4)
    
    # 背景色（深色半透明）
    bg_color = NSColor.colorWithCalibratedWhite_alpha_(0.15, 0.8)
    bg_color.set()
    bg_path.fill()
    
    # 边框
    border_color = NSColor.colorWithCalibratedWhite_alpha_(0.4, 0.6)
    border_color.set()
    bg_path.setLineWidth_(1.0)
    bg_path.stroke()
    
    # 计算进度条尺寸
    padding = 3
    bar_width = width - 2 * padding
    bar_height = 4
    bar_y = height - padding - bar_height
    filled_width = max(1, int(bar_width * percent / 100))
    
    # 绘制进度条背景
    bar_bg_rect = NSMakeRect(padding, bar_y, bar_width, bar_height)
    bar_bg_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bar_bg_rect, 2, 2)
    bar_bg_color = NSColor.colorWithCalibratedWhite_alpha_(0.3, 0.5)
    bar_bg_color.set()
    bar_bg_path.fill()
    
    # 根据使用率选择颜色（40%以下绿 / 40~60%黄 / 60~80%橙 / 80%+红）
    if percent >= 80:
        bar_color = NSColor.redColor()
    elif percent >= 60:
        bar_color = NSColor.orangeColor()
    elif percent >= 40:
        bar_color = NSColor.yellowColor()
    else:
        bar_color = NSColor.greenColor()
    
    # 绘制进度条填充
    if filled_width > 0:
        bar_fill_rect = NSMakeRect(padding, bar_y, filled_width, bar_height)
        bar_fill_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bar_fill_rect, 2, 2)
        bar_color.set()
        bar_fill_path.fill()
    
    # 绘制百分比文字（小号）
    text = f"{percent:.0f}%"
    from AppKit import NSFont, NSFontAttributeName, NSForegroundColorAttributeName, \
        NSMakePoint, NSAttributedString, NSBaselineOffsetAttributeName
    
    font = NSFont.systemFontOfSize_(8)
    text_color = NSColor.whiteColor()
    
    attrs = {
        NSFontAttributeName: font,
        NSForegroundColorAttributeName: text_color,
    }
    
    attr_str = NSAttributedString.alloc().initWithString_attributes_(text, attrs)
    text_size = attr_str.size()
    text_x = (width - text_size.width) / 2
    text_y = bar_y - text_size.height - 1
    
    attr_str.drawAtPoint_(NSMakePoint(text_x, text_y))
    
    image.unlockFocus()
    
    return image


def create_simple_icon(percent: float, width: int = 22, height: int = 22) -> Optional[object]:
    """
    创建简化版图标（仅进度条，无文字），适合小尺寸状态栏。
    """
    if not _HAS_PYOBJC:
        return None

    from AppKit import NSImage, NSBezierPath, NSColor, NSMakeRect, NSMakeSize, \
        NSGraphicsContext

    size = NSMakeSize(width, height)
    image = NSImage.alloc().initWithSize_(size)
    image.lockFocus()

    # 背景圆
    center_x = width / 2
    center_y = height / 2
    radius = min(width, height) / 2 - 2
    
    bg_path = NSBezierPath.bezierPath()
    bg_path.appendBezierPathWithArcWithCenter_startAngle_endAngle_clockwise_(
        (center_x, center_y), radius, 0, 360, True
    )
    bg_color = NSColor.colorWithCalibratedWhite_alpha_(0.12, 0.85)
    bg_color.set()
    bg_path.fill()
    
    # 圆弧进度
    if percent > 0:
        arc_path = NSBezierPath.bezierPath()
        start_angle = 90  # 从12点钟方向开始
        end_angle = start_angle - (percent / 100 * 360)
        arc_path.appendBezierPathWithArcWithCenter_startAngle_endAngle_clockwise_(
            (center_x, center_y), radius - 1, start_angle, end_angle, False
        )
        
        if percent >= 80:
            arc_color = NSColor.redColor()
        elif percent >= 60:
            arc_color = NSColor.orangeColor()
        elif percent >= 50:
            arc_color = NSColor.yellowColor()
        else:
            arc_color = NSColor.greenColor()
        
        arc_color.set()
        arc_path.setLineWidth_(2.5)
        arc_path.stroke()
    
    # 中心百分比文字
    from AppKit import NSFont, NSFontAttributeName, NSForegroundColorAttributeName, \
        NSMakePoint, NSAttributedString
    
    text = f"{percent:.0f}"
    font = NSFont.boldSystemFontOfSize_(9)
    text_color = NSColor.whiteColor()
    
    attrs = {
        NSFontAttributeName: font,
        NSForegroundColorAttributeName: text_color,
    }
    attr_str = NSAttributedString.alloc().initWithString_attributes_(text, attrs)
    text_size = attr_str.size()
    text_x = (width - text_size.width) / 2
    text_y = (height - text_size.height) / 2
    
    attr_str.drawAtPoint_(NSMakePoint(text_x, text_y))
    
    image.unlockFocus()
    return image


def icon_to_rumps(icon_obj) -> Optional[object]:
    """
    将 NSImage 转换为 rumps 可用的图标格式。
    rumps 接受 NSImage 或 file path。
    """
    return icon_obj


def get_icon_for_percent(percent: float, style: str = "bar") -> Optional[object]:
    """
    根据使用率获取合适的图标。
    
    style: "bar" = 进度条, "circle" = 圆形进度环
    """
    if style == "circle":
        return create_simple_icon(percent)
    return create_progress_icon(percent)


# ============================================================
# 测试入口
# ============================================================
if __name__ == "__main__":
    import sys
    
    print(f"PyObjC 可用: {_HAS_PYOBJC}")
    
    if _HAS_PYOBJC:
        # 测试不同使用率的图标
        for pct in [0, 25, 50, 75, 90, 100]:
            icon = create_progress_icon(pct)
            print(f"  {pct}%: 图标已创建 (size={icon.size()})")
        
        # 测试圆形图标
        for pct in [0, 50, 100]:
            icon = create_simple_icon(pct)
            print(f"  Circle {pct}%: 图标已创建 (size={icon.size()})")
        
        print("✅ 图标模块测试通过")
    else:
        print("⚠️ PyObjC 不可用，图标功能将降级为纯文本")