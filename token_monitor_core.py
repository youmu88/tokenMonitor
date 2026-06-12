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
QUOTA_API_URL = "https://token.woa.com/api/query-quota?platform=codebuddy"
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


def extract_model_stats_from_table(headers, rows):
    """
    从表格数据中提取模型使用量统计。
    尝试识别包含模型名称、使用量、配额等信息的表格列。
    
    返回: List[Dict] 格式为 [{"name": ..., "used": ..., "total": ..., "percent": ...}]
    """
    model_stats = []
    
    if not headers or not rows:
        return model_stats
    
    # 尝试识别列索引
    name_idx = -1
    used_idx = -1
    total_idx = -1
    percent_idx = -1
    
    for i, h in enumerate(headers):
        hl = h.lower()
        if any(kw in hl for kw in ["模型", "名称", "name", "model", "应用", "app"]):
            name_idx = i
        elif any(kw in hl for kw in ["已用", "使用量", "消耗", "用量", "used", "consumed", "usage"]):
            used_idx = i
        elif any(kw in hl for kw in ["总量", "配额", "限额", "总配额", "total", "quota", "limit", "capacity"]):
            total_idx = i
        elif any(kw in hl for kw in ["使用率", "占比", "百分比", "percent", "rate", "ratio"]):
            percent_idx = i
    
    # 如果没找到明确的列名，尝试根据数据特征推断
    if name_idx == -1 and rows:
        # 第一列通常是名称
        name_idx = 0
    
    for row in rows:
        if len(row) < 2:
            continue
        
        # 获取模型名称
        name = ""
        if name_idx >= 0 and name_idx < len(row):
            name = row[name_idx].strip()
        
        if not name or len(name) < 2:
            continue
        
        # 跳过表头行或汇总行
        if any(kw in name.lower() for kw in ["合计", "总计", "汇总", "total", "sum", "全部"]):
            continue
        
        entry = {"name": name}
        
        # 解析使用量
        if used_idx >= 0 and used_idx < len(row):
            val = parse_numeric_value(row[used_idx])
            if val is not None:
                entry["used"] = val
        
        # 解析总量
        if total_idx >= 0 and total_idx < len(row):
            val = parse_numeric_value(row[total_idx])
            if val is not None:
                entry["total"] = val
        
        # 解析百分比
        if percent_idx >= 0 and percent_idx < len(row):
            pct = parse_percentage_value(row[percent_idx])
            if pct is not None:
                entry["percent"] = pct
        
        # 如果没找到百分比但找到了 used 和 total，计算
        if "percent" not in entry and "used" in entry and "total" in entry:
            if entry["total"] > 0:
                entry["percent"] = round(entry["used"] / entry["total"] * 100, 1)
        
        # 只保留有至少一个数值字段的条目
        if "used" in entry or "total" in entry or "percent" in entry:
            model_stats.append(entry)
    
    return model_stats


def parse_numeric_value(text):
    """从文本中解析数值（支持 K/M/B 单位、逗号分隔、¥符号）"""
    if not text:
        return None
    
    text = text.strip()
    # 去除货币符号
    text = re.sub(r'[¥$€£]', '', text)
    
    # 处理 K/M/B 单位
    multiplier = 1
    if re.search(r'[kK]', text):
        multiplier = 1000
        text = re.sub(r'[kK]', '', text)
    elif re.search(r'[mM]', text):
        multiplier = 1000000
        text = re.sub(r'[mM]', '', text)
    elif re.search(r'[bB]', text):
        multiplier = 1000000000
        text = re.sub(r'[bB]', '', text)
    
    # 提取数字
    nums = re.findall(r'[\d,]+(?:\.\d+)?', text)
    if nums:
        try:
            val = float(nums[0].replace(',', ''))
            return int(val * multiplier)
        except ValueError:
            pass
    return None


def parse_percentage_value(text):
    """从文本中解析百分比值"""
    if not text:
        return None
    text = text.strip()
    match = re.search(r'([\d.]+)\s*%', text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None


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


TOKEN_CONTEXT_KEYWORDS = (
    "token", "配额", "额度", "费用", "使用率", "调用", "次数", "限额", "余量", "已用", "已使用",
    "剩余", "总量", "总配额", "总限额", "quota", "usage", "used", "limit", "consumed",
    "remain", "remaining",
)
MIN_REASONABLE_TOTAL = 100
MAX_REASONABLE_PERCENT = 150.0


def _has_token_context(text: str) -> bool:
    lower_text = text.lower()
    return any(keyword in lower_text for keyword in TOKEN_CONTEXT_KEYWORDS)


def _finalize_token_result(used, total=MAX_TOKEN, percentage=None):
    used = int(float(used))
    total = int(float(total))
    if total <= 0 or used < 0:
        return None
    if percentage is None:
        percentage = round(used / total * 100, 1)
    else:
        percentage = round(float(percentage), 1)
    if total < MIN_REASONABLE_TOTAL or percentage < 0 or percentage > MAX_REASONABLE_PERCENT:
        logger.warning(f"忽略疑似误解析的 Token 数据: used={used}, total={total}, percent={percentage}%")
        return None
    return used, total, percentage


def parse_quota_api_data(data):
    """解析 /api/query-quota 返回的真实额度数据。"""
    if not isinstance(data, dict) or data.get("success") is not True:
        return None

    used = data.get("total_used")
    total = data.get("total_quota")
    percentage = data.get("total_usage_rate")
    if percentage is None:
        percentage = data.get("usage_percentage") or data.get("group_usage_rate")
    if used is None or total is None:
        return None
    return _finalize_token_result(used, total, percentage)


def parse_token_value(text):
    """
    从页面文本中解析 token/费用使用量。
    优先识别真实额度卡片中的金额格式，避免把提问内容、日期、分页里的 `6/3` 误判为配额。
    返回 (used, total, percentage) 或 None。
    """
    if not text:
        return None

    quota_money_match = re.search(
        r'已用\s*[¥$]\s*([\d,]+(?:\.\d+)?)\s*/\s*[¥$]\s*([\d,]+(?:\.\d+)?)',
        text,
        re.DOTALL,
    )
    if quota_money_match:
        used = quota_money_match.group(1).replace(',', '')
        total = quota_money_match.group(2).replace(',', '')
        pct_match = re.search(r'已用\s*([\d.]+)%', text)
        percentage = float(pct_match.group(1)) if pct_match else None
        result = _finalize_token_result(used, total, percentage)
        if result:
            logger.debug(f"Token 金额卡片解析命中: {quota_money_match.group(0)[:80]}")
            return result

    used = None
    total = MAX_TOKEN
    percentage = None

    used_match = re.search(r'(?:已用|使用|已使用|调用次数|消耗)[：:]?\s*[¥$]?\s*([\d,]+(?:\.\d+)?)', text)
    if used_match:
        used = used_match.group(1).replace(',', '')

    total_match = re.search(r'(?:总量|总配额|总限额|总次数|总调用|限额|额度|配额)[：:]?\s*[¥$]?\s*([\d,]+(?:\.\d+)?)', text)
    if total_match:
        total = total_match.group(1).replace(',', '')

    pct_match = re.search(r'(?:使用率|使用比例|占比|已用)[：:]?\s*([\d.]+)%', text)
    if pct_match:
        percentage = float(pct_match.group(1))

    if used is not None:
        result = _finalize_token_result(used, total, percentage)
        if result:
            return result

    for ratio_match in re.finditer(r'(?<!\d)(\d[\d,]*)\s*/\s*(\d[\d,]*)(?!\d)', text):
        start, end = ratio_match.span()
        context = text[max(0, start - 100):min(len(text), end + 100)]
        if not _has_token_context(context):
            logger.debug(f"跳过无 Token 上下文的比例片段: {ratio_match.group(0)}")
            continue
        result = _finalize_token_result(
            ratio_match.group(1).replace(',', ''),
            ratio_match.group(2).replace(',', ''),
        )
        if result:
            logger.debug(f"Token 比例解析命中: {ratio_match.group(0)}")
            return result

    return None


def parse_token_from_json(json_data):
    """从 JSON 数据中递归查找 token 使用量，避免 value/count/total 等通用字段误判。"""
    if not json_data:
        return None

    strict_used_keys = ('used', 'usedTokens', 'used_tokens', 'consumed', 'consumedTokens', 'callCount', 'call_count')
    contextual_used_keys = strict_used_keys + ('usage', 'current', 'value', 'count')
    total_keys = ('total', 'totalTokens', 'total_tokens', 'quota', 'limit', 'capacity', 'maxTokens', 'max_tokens')

    def search(obj, depth=0, path=""):
        if depth > 10:
            return None
        if isinstance(obj, dict):
            key_text = " ".join([path, *map(str, obj.keys())]).lower()
            has_context = any(keyword in key_text for keyword in TOKEN_CONTEXT_KEYWORDS)
            used_keys = contextual_used_keys if has_context else strict_used_keys

            candidate_used = None
            candidate_total = MAX_TOKEN
            for key in used_keys:
                if key in obj and isinstance(obj[key], (int, float)):
                    candidate_used = obj[key]
                    break
            for key in total_keys:
                if key in obj and isinstance(obj[key], (int, float)):
                    candidate_total = obj[key]
                    break

            if candidate_used is not None:
                result = _finalize_token_result(candidate_used, candidate_total)
                if result:
                    logger.debug(f"JSON Token 解析命中路径: {path or '<root>'}")
                    return result

            for key, value in obj.items():
                result = search(value, depth + 1, f"{path}.{key}" if path else str(key))
                if result:
                    return result
        elif isinstance(obj, list):
            for index, item in enumerate(obj):
                result = search(item, depth + 1, f"{path}[{index}]")
                if result:
                    return result
        return None

    return search(json_data)


async def fetch_quota_api_data(ctx):
    """优先调用真实额度接口获取数据。"""
    try:
        response = await ctx.request.get(QUOTA_API_URL, timeout=30000)
        if not response.ok:
            logger.warning(f"额度接口请求失败: HTTP {response.status}")
            return None
        data = await response.json()
        result = parse_quota_api_data(data)
        if not result:
            logger.warning("额度接口响应未包含可用额度数据")
            return None
        used, total, percentage = result
        logger.info(f"额度接口解析成功: used={used}, total={total}, percent={percentage}%")
        return {
            "success": True,
            "used": used,
            "total": total,
            "percentage": percentage,
            "parse_source": "quota_api",
            "raw_json": data,
        }
    except Exception as e:
        logger.warning(f"额度接口调用失败: {e}")
        return None


async def fetch_token_data(ctx):
    """
    从 token 页面抓取数据，返回结构化结果。
    返回: dict with keys: success, used, total, percentage, raw_text, error
    """
    page = None
    try:
        api_result = await fetch_quota_api_data(ctx)
        if api_result:
            api_result["title"] = "Token 看板"
            return api_result

        page = await ctx.new_page()
        page.set_default_timeout(30000)
        await page.goto(TARGET_URL, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)

        current_url = page.url
        if "passport" in current_url or "signin" in current_url:
            return {"success": False, "error": "未登录", "url": current_url}

        # 部分登录态需要先访问页面后接口才可用，再尝试一次接口。
        api_result = await fetch_quota_api_data(ctx)
        if api_result:
            api_result["title"] = await page.title()
            return api_result

        page_html = await page.content()
        page_text = await page.inner_text("body")

        result = None
        parse_source = None

        quota_text = await page.evaluate("""
            () => {
                const el = document.querySelector('.quota-overview-card');
                return el ? el.innerText : '';
            }
        """)
        if quota_text:
            result = parse_token_value(quota_text)
            if result:
                parse_source = "quota-overview-card"

        # 2. 尝试表格解析（同时提取模型统计数据）
        model_stats = []
        headers, table_rows = extract_table_data(page_html)
        if table_rows:
            # 从表格中提取模型使用量统计（无论 token 数据是否已解析，都执行）
            model_stats = extract_model_stats_from_table(headers, table_rows)
            if not result:
                all_text = json.dumps(table_rows)
                result = parse_token_value(all_text)

        # 3. 尝试文本解析
        if not result:
            result = parse_token_value(page_text)
            if result:
                parse_source = "page_text"

        if not result:
            json_data = extract_json_data(page_html)
            if json_data:
                result = parse_token_from_json(json_data)
                if result:
                    parse_source = "json"

        if not result:
            text_data = extract_token_data(page_text)
            for line in text_data:
                result = parse_token_value(line)
                if result:
                    parse_source = "keyword_line"
                    break

        if result:
            used, total, percentage = result
            logger.info(f"Token 解析成功: source={parse_source}, used={used}, total={total}, percent={percentage}%")
            return {
                "success": True,
                "used": used,
                "total": total,
                "percentage": percentage,
                "parse_source": parse_source,
                "raw_text": page_text[:500],
                "title": await page.title(),
                "model_stats": model_stats,
            }
        else:
            return {
                "success": True,
                "used": None,
                "total": MAX_TOKEN,
                "percentage": None,
                "raw_text": page_text[:500],
                "title": await page.title(),
                "warning": "未能解析出 token 数据，请检查页面结构",
                "model_stats": model_stats,
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
    """创建浏览器上下文（固定 profile 优先，CDP 仅显式开启，临时目录兜底）"""
    # CDP 会连接用户正在使用的 Chrome，可能造成卡顿或误关闭；默认关闭，仅显式设置环境变量时启用。
    if os.environ.get("TOKEN_MONITOR_USE_CDP") == "1":
        try:
            p = await async_playwright().start()
            browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
            ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
            logger.info("已通过 CDP 连接到 Chrome")
            return p, browser, ctx, "cdp"
        except Exception as e:
            logger.warning(f"CDP 连接失败: {e}，改用独立浏览器上下文")

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