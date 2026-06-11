#!/usr/bin/env python3
"""
Token Monitor Core - 核心抓取引擎
==================================
从 https://token.woa.com/ 抓取 token 使用数据，供状态栏应用使用。
复用原 token_monitor.py 的核心逻辑，去除 Rich 展示依赖。

v2.1 - 修复 Cookie 持久化问题：
- 使用固定的 user_data_dir 保存浏览器 profile（含 cookies、localStorage）
- 避免每次启动都创建新的临时目录导致 cookie 失效
- 登录流程和监控流程共享同一个 profile 目录

v2.2 - 修复 py2app 打包后 playwright driver 路径问题：
- py2app 将 playwright 包压缩到 python314.zip 中，导致 driver/node 无法作为可执行文件访问
- 通过 monkey-patch playwright._impl._driver.compute_driver_executable，
  将 driver 路径指向 Resources 中的 driver 目录（而非 zip 内的路径）
"""

# BUGFIX v2.2: 在 import playwright.async_api 之前，monkey-patch driver 路径
# py2app 打包后 playwright 包被压缩到 python314.zip 中，
# inspect.getfile(playwright) 返回 zip 内路径，driver/node 无法执行。
# 解决方案：手动设置 driver 路径指向 Resources 中的 driver 目录。
import os as _os
import sys as _sys

# 检测是否在 py2app 打包环境中运行
# py2app 设置 sys.frozen = 'py2app'（字符串），而非 True
_in_py2app = getattr(_sys, 'frozen', None) is not None

if _in_py2app:
    # 在 py2app 环境中，driver 目录在 Resources 中
    _resources_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)))
    _driver_dir = _os.path.join(_resources_dir, "driver")
    if _os.path.isdir(_driver_dir):
        _node_path = _os.path.join(_driver_dir, "node")
        _cli_path = _os.path.join(_driver_dir, "package", "cli.js")
        if _os.path.isfile(_node_path):
            _os.environ["PLAYWRIGHT_NODEJS_PATH"] = _node_path
        
        # 先 import _driver 模块，然后 patch compute_driver_executable
        import playwright._impl._driver as _pdriver
        
        def _patched_compute():
            """返回 Resources 中 driver 目录的路径"""
            return (_node_path, _cli_path)
        
        _pdriver.compute_driver_executable = _patched_compute
else:
    # 非 py2app 环境，使用系统 node（如果有）
    _node_path = _os.popen("which node 2>/dev/null").read().strip()
    if _node_path and _os.path.isfile(_node_path):
        _os.environ.setdefault("PLAYWRIGHT_NODEJS_PATH", _node_path)

import asyncio
import json
import os
import re
import shutil
import tempfile
import time
import logging
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

logger = logging.getLogger("token_monitor_core")

# ============================================================
# 配置
# ============================================================
TARGET_URL = "https://token.woa.com/"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(APP_DIR, ".token_monitor_cookies.json")
# BUGFIX: 使用固定的 profile 目录，而非每次创建临时目录
# 这样登录后的 cookies/localStorage 会被持久化，下次启动无需重新登录
PROFILE_DIR = os.path.join(APP_DIR, ".token_monitor_profile")
CDP_PORT = 9222
MAX_TOKEN = 4060  # 总配额


def extract_token_data(page_text):
    """从页面文本中提取 token 使用数据"""
    data_rows = []
    lines = page_text.split("\n")
    for line in lines:
        line_stripped = line.strip()
        if any(kw in line_stripped.lower() for kw in [
            "token", "配额", "使用率", "调用", "次数", "限额",
            "余量", "已用", "剩余", "总量", "qps", "tps",
        ]):
            clean = re.sub(r'<[^>]+>', '', line_stripped).strip()
            if clean and len(clean) > 3:
                data_rows.append(clean)
    return data_rows


def extract_table_data(page_html):
    """从页面 HTML 中提取表格数据"""
    headers = []
    rows = []
    tables = re.findall(r'<table[^>]*>(.*?)</table>', page_html, re.DOTALL)
    for table_html in tables:
        ths = re.findall(r'<th[^>]*>(.*?)</th>', table_html, re.DOTALL)
        if ths:
            headers = [re.sub(r'<[^>]+>', '', h).strip() for h in ths]
        trs = re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL)
        for tr in trs:
            tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.DOTALL)
            if tds:
                row = [re.sub(r'<[^>]+>', '', td).strip() for td in tds]
                rows.append(row)
    return headers, rows


def extract_json_data(page_text):
    """尝试从页面中提取 JSON 格式的数据"""
    patterns = [
        r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
        r'window\.__DATA__\s*=\s*({.*?});',
        r'window\.__NUXT__\s*=\s*({.*?});',
        r'window\.__PRELOADED_STATE__\s*=\s*({.*?});',
        r'<script[^>]*>\s*window\.[^=]+=\s*({.*?})\s*</script>',
        r'<pre[^>]*>({.*?})</pre>',
        r'<code[^>]*>({.*?})</code>',
    ]
    for pattern in patterns:
        match = re.search(pattern, page_text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
    return None


def parse_token_value(text):
    """
    从页面文本中智能解析 token 使用量。
    支持两种格式：
    1. 金额格式：¥862.06 / ¥4,060.00  已用 21.2%
    2. 数字格式：862/4060  或  已用: 862  总量: 4060
    返回 (used, total, percentage) 或 None。
    """
    used = None
    total = MAX_TOKEN
    percentage = None

    # ================================================================
    # 策略1: 匹配金额格式 "¥862.06 / ¥4,060.00" 或 "已用 ¥862.06"
    # ================================================================
    # 匹配 "已用 ¥862.06" 或 "已用 ¥862.06 / ¥4,060.00"
    money_used_match = re.search(r'已用\s*[¥$]\s*([\d,]+(?:\.\d+)?)', text)
    if money_used_match:
        used_str = money_used_match.group(1).replace(',', '')
        used = float(used_str)
        # 尝试匹配总额度
        money_total_match = re.search(r'/\s*[¥$]\s*([\d,]+(?:\.\d+)?)', text)
        if money_total_match:
            total_str = money_total_match.group(1).replace(',', '')
            total = float(total_str)
        # 尝试匹配百分比 "已用 21.2%" 或 "21.2%"
        pct_match = re.search(r'已用\s*([\d.]+)%', text)
        if pct_match:
            percentage = float(pct_match.group(1))
        elif total > 0:
            percentage = round(used / total * 100, 1)
        # 金额格式的 used/total 可能是浮点数，转为 int（向下取整）
        used = int(used)
        total = int(total)
        return used, total, percentage

    # ================================================================
    # 策略2: 匹配纯数字格式 "xxx/4060" 或 "xxx / 4060"
    # ================================================================
    ratio_match = re.search(r'(\d[\d,]*)\s*/\s*(\d[\d,]*)', text)
    if ratio_match:
        used = int(ratio_match.group(1).replace(',', ''))
        total = int(ratio_match.group(2).replace(',', ''))
        if total > 0:
            percentage = round(used / total * 100, 1)
        return used, total, percentage

    # 尝试匹配 "已用: xxx" 或 "使用: xxx"
    used_match = re.search(r'(?:已用|使用|已使用|调用次数|消耗)[：:]\s*([\d,]+)', text)
    if used_match:
        used = int(used_match.group(1).replace(',', ''))

    # 尝试匹配 "总量: xxx" 或 "总配额: xxx"
    total_match = re.search(r'(?:总量|总配额|总限额|总次数|总调用)[：:]\s*([\d,]+)', text)
    if total_match:
        total = int(total_match.group(1).replace(',', ''))

    # 尝试匹配 "使用率: yy%" 或 "yy%"
    pct_match = re.search(r'(?:使用率|使用比例|占比)[：:]\s*([\d.]+)%', text)
    if pct_match:
        percentage = float(pct_match.group(1))

    if used is not None:
        if percentage is None and total > 0:
            percentage = round(used / total * 100, 1)
        return used, total, percentage

    return None


def parse_token_from_json(json_data):
    """从 JSON 数据中递归查找 token 使用量"""
    if not json_data:
        return None

    used = None
    total = MAX_TOKEN

    def search(obj, depth=0):
        nonlocal used, total
        if depth > 10:
            return
        if isinstance(obj, dict):
            for key in ['used', 'usedTokens', 'used_tokens', 'consumed', 'consumedTokens',
                         'usage', 'current', 'value', 'count', 'callCount', 'call_count']:
                if key in obj and isinstance(obj[key], (int, float)):
                    used = int(obj[key])
                    break
            for key in ['total', 'totalTokens', 'total_tokens', 'quota', 'limit',
                         'max', 'capacity', 'maxTokens', 'max_tokens']:
                if key in obj and isinstance(obj[key], (int, float)):
                    total = int(obj[key])
                    break
            for v in obj.values():
                search(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                search(item, depth + 1)

    search(json_data)
    if used is not None:
        pct = round(used / total * 100, 1) if total > 0 else 0
        return used, total, pct
    return None


async def fetch_token_data(ctx):
    """
    从 token 页面抓取数据，返回结构化结果。
    返回: dict with keys: success, used, total, percentage, raw_text, error
    """
    page = None
    try:
        page = await ctx.new_page()
        page.set_default_timeout(30000)
        await page.goto(TARGET_URL, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)

        current_url = page.url
        if "passport" in current_url or "signin" in current_url:
            return {"success": False, "error": "未登录", "url": current_url}

        page_html = await page.content()
        page_text = await page.inner_text("body")

        # 尝试多种解析方式
        result = None

        # 1. 尝试 JSON 解析
        json_data = extract_json_data(page_html)
        if json_data:
            result = parse_token_from_json(json_data)

        # 2. 尝试表格解析
        if not result:
            headers, table_rows = extract_table_data(page_html)
            if table_rows:
                all_text = json.dumps(table_rows)
                result = parse_token_value(all_text)

        # 3. 尝试文本解析
        if not result:
            result = parse_token_value(page_text)

        # 4. 尝试文本行逐行解析
        if not result:
            text_data = extract_token_data(page_text)
            for line in text_data:
                result = parse_token_value(line)
                if result:
                    break

        if result:
            used, total, percentage = result
            return {
                "success": True,
                "used": used,
                "total": total,
                "percentage": percentage,
                "raw_text": page_text[:500],
                "title": await page.title(),
            }
        else:
            return {
                "success": True,
                "used": None,
                "total": MAX_TOKEN,
                "percentage": None,
                "raw_text": page_text[:500],
                "title": await page.title(),
                "warning": "未能解析出 token 数据，请检查页面结构"
            }

    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if page:
            await page.close()


async def run_login_flow():
    """打开浏览器让用户登录 OA，保存 cookie 后退出。
    
    BUGFIX v2.1: 使用固定的 PROFILE_DIR 保存浏览器 profile，
    登录后的 cookies/localStorage 会被持久化，下次启动无需重新登录。
    """
    print("=" * 60)
    print("🔑 Token 监控 - 首次登录认证")
    print("=" * 60)
    print("将在浏览器中打开 token.woa.com，请完成 OA 登录。")
    print("登录成功后，浏览器 profile 会自动保存，状态栏应用即可开始监控。")
    print()

    async with async_playwright() as p:
        # BUGFIX: 使用固定的 profile 目录，而非临时目录
        os.makedirs(PROFILE_DIR, exist_ok=True)
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        page = await ctx.new_page()
        await page.goto(TARGET_URL, timeout=30000)
        print("⏳ 请在浏览器中完成 OA 登录...")
        print("⏳ 登录成功后，脚本会自动检测并保存（最多等待 5 分钟）")

        for _ in range(300):
            await asyncio.sleep(1)
            current_url = page.url
            if "passport" not in current_url and "signin" not in current_url:
                await asyncio.sleep(3)
                # 同时导出 cookies 到 JSON 文件（兼容旧逻辑）
                cookies = await ctx.cookies()
                with open(COOKIE_FILE, "w") as f:
                    json.dump(cookies, f, indent=2)
                print(f"✅ 浏览器 Profile 已保存到: {PROFILE_DIR}")
                print(f"✅ Cookies 已导出到: {COOKIE_FILE}")
                print("🎉 登录成功！可以启动状态栏监控了。")
                await page.close()
                await ctx.close()
                return True

        print("❌ 登录超时（5分钟），请重试。")
        await page.close()
        await ctx.close()
        return False


async def load_cookies(ctx):
    """从文件加载 cookies 到浏览器上下文"""
    if not os.path.exists(COOKIE_FILE):
        return False
    with open(COOKIE_FILE, "r") as f:
        cookies = json.load(f)
    await ctx.add_cookies(cookies)
    logger.info(f"已加载 {len(cookies)} 个 cookies")
    return True


def has_profile():
    """检查是否有保存的浏览器 profile"""
    return os.path.isdir(PROFILE_DIR) and os.path.exists(os.path.join(PROFILE_DIR, "Default"))


async def create_browser_context():
    """创建浏览器上下文（CDP 优先，固定 profile 次之，临时目录兜底）"""
    # 先尝试 CDP 连接
    try:
        p = await async_playwright().start()
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        logger.info("已通过 CDP 连接到 Chrome")
        return p, browser, ctx, "cdp"
    except Exception:
        pass

    # BUGFIX v2.1: 优先使用固定的 profile 目录（持久化登录态）
    if has_profile():
        try:
            p = await async_playwright().start()
            ctx = await p.chromium.launch_persistent_context(
                user_data_dir=PROFILE_DIR,
                headless=True,
                channel="chrome",
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            logger.info("已通过固定 Profile 模式启动（复用登录态）")
            return p, None, ctx, "profile"
        except Exception as e:
            logger.warning(f"固定 Profile 模式启动失败: {e}，尝试清理后重建...")
            # Profile 可能损坏，清理后让用户重新登录
            try:
                shutil.rmtree(PROFILE_DIR, ignore_errors=True)
            except Exception:
                pass

    # 兜底：使用临时目录 + cookie 文件（兼容旧逻辑）
    try:
        p = await async_playwright().start()
        temp_dir = tempfile.mkdtemp(prefix="token_monitor_")
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=temp_dir,
            headless=True,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        cookies_loaded = await load_cookies(ctx)
        if cookies_loaded:
            logger.info("已通过独立模式启动（使用保存的 cookie）")
            return p, None, ctx, "standalone"
        await ctx.close()
        await p.stop()
        return None, None, None, "no_auth"
    except Exception as e:
        logger.error(f"启动浏览器失败: {e}")
        return None, None, None, "error"


async def fetch_token_once():
    """
    一次性抓取 token 数据（供状态栏应用定时调用）。
    返回: dict with keys: success, used, total, percentage, error
    """
    p, browser, ctx, mode = await create_browser_context()
    if not ctx:
        return {"success": False, "error": "无法启动浏览器，请先运行 --login 登录", "mode": mode}

    try:
        result = await fetch_token_data(ctx)
        result["mode"] = mode
        return result
    finally:
        try:
            if browser:
                await browser.close()
            if p:
                await p.stop()
        except Exception:
            pass


def format_status_bar(used, total, percentage):
    """格式化状态栏显示文本"""
    if used is None:
        return "Tokens: ?/4060"
    return f"Tokens: {used}/{total}({percentage}%)"


# ============================================================
# TokenMonitorEngine 类 - 供状态栏应用使用
# ============================================================
class TokenMonitorEngine:
    """Token 监控引擎，封装抓取和登录逻辑，供状态栏应用调用"""

    def __init__(self):
        self.target_url = TARGET_URL
        self.cookie_file = COOKIE_FILE
        self.max_token = MAX_TOKEN
        self.cdp_port = CDP_PORT

    async def fetch_token_data(self):
        """
        抓取 token 数据。
        返回: dict with keys: success, used, total, percent, error
        """
        result = await fetch_token_once()
        # 统一字段名：percent vs percentage
        if "percentage" in result:
            result["percent"] = result["percentage"]
        return result

    async def run_login_flow(self):
        """运行登录流程"""
        return await run_login_flow()

    def has_cookies(self):
        """检查是否有保存的 cookie 或 profile"""
        return os.path.exists(self.cookie_file) or has_profile()


if __name__ == "__main__":
    import sys
    if "--login" in sys.argv:
        asyncio.run(run_login_flow())
    else:
        result = asyncio.run(fetch_token_once())
        if result["success"]:
            if result.get("used") is not None:
                print(format_status_bar(result["used"], result["total"], result["percentage"]))
            else:
                print(f"⚠️  页面已加载但未能解析数据: {result.get('warning', '')}")
                print(f"   标题: {result.get('title', 'N/A')}")
        else:
            print(f"❌ 抓取失败: {result.get('error', '未知错误')}")