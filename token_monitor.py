#!/usr/bin/env python3
"""
Token Monitor - 内网 token 使用情况定时监控工具
================================================
定时（默认 1 分钟）访问 https://token.woa.com/，抓取 token 使用情况并展示。

用法:
    python3 token_monitor.py                        # 默认 60 秒轮询（自动检测模式）
    python3 token_monitor.py --interval 30          # 30 秒轮询
    python3 token_monitor.py --once                 # 只抓一次
    python3 token_monitor.py --headless             # 无头模式
    python3 token_monitor.py --cdp                  # CDP 连接已运行的 Chrome（推荐）
    python3 token_monitor.py --standalone           # 独立模式（启动新 Chrome 实例）
    python3 token_monitor.py --login                # 仅执行登录流程（保存 cookie）

首次使用（推荐 --cdp 模式）:
    1. 关闭所有 Chrome 窗口
    2. 运行: python3 token_monitor.py --cdp
    3. 脚本自动启动 Chrome（调试模式），登录 OA 后即可开始监控

依赖:
    pip install playwright rich tabulate
    playwright install chromium
"""

import asyncio
import argparse
import sys
import os
import json
import tempfile
import subprocess
import re
import signal
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

console = Console()

# ============================================================
# 配置区 - 按需修改
# ============================================================
TARGET_URL = "https://token.woa.com/"
COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".token_monitor_cookies.json")
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
CHROME_USER_DATA_DIR = os.path.expanduser("~/Library/Application Support/Google/Chrome")
CDP_PORT = 9222


def parse_args():
    parser = argparse.ArgumentParser(
        description="Token 使用情况定时监控工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 token_monitor.py                        # 默认 60 秒轮询（自动检测模式）
  python3 token_monitor.py --interval 30          # 30 秒轮询
  python3 token_monitor.py --once                 # 只抓取一次
  python3 token_monitor.py --headless             # 无头模式
  python3 token_monitor.py --cdp                  # CDP 连接已运行的 Chrome（推荐）
  python3 token_monitor.py --standalone           # 独立模式（启动新 Chrome 实例）
  python3 token_monitor.py --login                # 仅执行登录流程（保存 cookie）
        """,
    )
    parser.add_argument("--interval", "-i", type=int, default=60,
                        help="轮询间隔（秒），默认 60")
    parser.add_argument("--once", "-o", action="store_true",
                        help="只抓取一次后退出")
    parser.add_argument("--headless", action="store_true",
                        help="无头模式（不显示浏览器窗口）")
    parser.add_argument("--cdp", action="store_true",
                        help="使用 CDP 连接已运行的 Chrome（推荐，复用登录态）")
    parser.add_argument("--standalone", action="store_true",
                        help="独立模式：启动新 Chrome 实例（需手动登录一次）")
    parser.add_argument("--login", action="store_true",
                        help="仅执行登录流程（保存 cookie 后退出），供 --standalone 模式使用")
    parser.add_argument("--user-data-dir", type=str, default=CHROME_USER_DATA_DIR,
                        help=f"Chrome 用户数据目录路径（默认: {CHROME_USER_DATA_DIR}）")
    return parser.parse_args()


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


def create_display_table(data_rows, headers=None):
    """创建 Rich 表格用于展示"""
    if not data_rows:
        table = Table(title="📊 Token 使用情况", box=box.ROUNDED)
        table.add_column("信息", style="dim")
        table.add_row("暂无数据")
        return table
    if headers and len(headers) > 1:
        table = Table(title="📊 Token 使用情况", box=box.ROUNDED)
        for h in headers:
            table.add_column(h, style="cyan")
        for row in data_rows:
            table.add_row(*[str(c) for c in row])
    else:
        table = Table(title="📊 Token 使用情况", box=box.ROUNDED)
        table.add_column("指标", style="cyan")
        table.add_column("值", style="green")
        for item in data_rows:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                table.add_row(str(item[0]), str(item[1]))
            elif isinstance(item, str):
                if ":" in item:
                    parts = item.split(":", 1)
                    table.add_row(parts[0].strip(), parts[1].strip())
                else:
                    table.add_row(item, "")
    return table


async def fetch_token_page(ctx, url, args):
    """打开 token 页面，等待加载完成，提取数据"""
    page = None
    try:
        page = await ctx.new_page()
        page.set_default_timeout(30000)
        start_time = time.time()
        await page.goto(url, wait_until="networkidle", timeout=30000)
        load_time = time.time() - start_time
        await asyncio.sleep(2)
        current_url = page.url
        if "passport" in current_url or "signin" in current_url:
            return {"success": False, "error": "未登录，请先在 Chrome 中登录 OA 后重试", "url": current_url, "load_time": load_time}
        page_html = await page.content()
        page_text = await page.inner_text("body")
        json_data = extract_json_data(page_html)
        headers, table_rows = extract_table_data(page_html)
        text_data = extract_token_data(page_text)
        screenshot_path = None
        if not headers and not text_data and not json_data:
            os.makedirs(SCREENSHOT_DIR, exist_ok=True)
            screenshot_path = os.path.join(SCREENSHOT_DIR, f"token_page_{int(time.time())}.png")
            await page.screenshot(path=screenshot_path, full_page=True)
        return {
            "success": True, "url": current_url, "title": await page.title(),
            "load_time": load_time, "headers": headers, "table_rows": table_rows,
            "text_data": text_data, "json_data": json_data, "screenshot_path": screenshot_path,
        }
    except Exception as e:
        return {"success": False, "error": str(e), "url": url}
    finally:
        if page:
            await page.close()


def display_data(result):
    """展示抓取到的数据"""
    headers = result.get("headers", [])
    table_rows = result.get("table_rows", [])
    text_data = result.get("text_data", [])
    json_data = result.get("json_data")
    displayed = False
    if headers and table_rows:
        console.print(create_display_table(table_rows, headers))
        displayed = True
    if json_data and not displayed:
        from rich.json import JSON
        console.print(Panel(JSON(json_data, indent=2), title="📦 JSON 数据", border_style="green"))
        displayed = True
    if text_data and not displayed:
        table = Table(title="📊 Token 使用数据（文本行）", box=box.ROUNDED)
        table.add_column("#", style="dim", width=4)
        table.add_column("内容", style="cyan")
        for i, line in enumerate(text_data[:30], 1):
            table.add_row(str(i), line)
        console.print(table)
        if len(text_data) > 30:
            console.print(f"[dim]... 还有 {len(text_data) - 30} 行未显示[/dim]")
        displayed = True
    if not displayed:
        console.print(Panel(
            f"标题: {result.get('title', 'N/A')}\nURL: {result.get('url', 'N/A')}\n加载耗时: {result['load_time']:.2f}s",
            title="📄 页面信息", border_style="yellow",
        ))
        console.print("[yellow]⚠ 未能从页面中解析出结构化数据，可能需要调整解析逻辑。[/yellow]")
        console.print("[dim]💡 可先使用 --once 模式查看页面内容，然后调整 extract_* 函数。[/dim]")


async def save_cookies(ctx, filepath):
    """保存当前上下文的 cookies 到文件"""
    cookies = await ctx.cookies()
    with open(filepath, "w") as f:
        json.dump(cookies, f, indent=2)
    console.print(f"[green]✅ Cookies 已保存到: {filepath}[/green]")


async def load_cookies(ctx, filepath):
    """从文件加载 cookies 到上下文"""
    if not os.path.exists(filepath):
        return False
    with open(filepath, "r") as f:
        cookies = json.load(f)
    await ctx.add_cookies(cookies)
    console.print(f"[green]✅ 已从 {filepath} 加载 {len(cookies)} 个 cookies[/green]")
    return True


async def run_login_flow(args):
    """仅执行登录流程：打开浏览器让用户登录，保存 cookie 后退出"""
    console.print(Panel.fit("[bold cyan]🔑 Token 监控 - 登录流程[/bold cyan]\n"
                            "请在弹出的浏览器窗口中登录 OA 系统。\n"
                            "登录成功后，脚本会自动保存 cookie 并退出。",
                            border_style="cyan"))
    async with async_playwright() as p:
        temp_dir = tempfile.mkdtemp(prefix="token_monitor_login_")
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=temp_dir,
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        page = await ctx.new_page()
        await page.goto(TARGET_URL, timeout=30000)
        console.print("[yellow]⏳ 请在浏览器中完成 OA 登录...[/yellow]")
        console.print("[yellow]⏳ 登录成功后，脚本会自动检测并保存 cookie（最多等待 5 分钟）[/yellow]")
        for _ in range(300):
            await asyncio.sleep(1)
            current_url = page.url
            if "passport" not in current_url and "signin" not in current_url:
                await asyncio.sleep(3)
                await save_cookies(ctx, COOKIE_FILE)
                console.print("[green]🎉 登录成功！Cookies 已保存，可以开始监控了。[/green]")
                console.print(f"[green]运行: python3 {sys.argv[0]} --standalone[/green]")
                await page.close()
                await ctx.close()
                return
        console.print("[red]❌ 登录超时（5分钟），请重试。[/red]")
        await page.close()
        await ctx.close()


async def run_standalone_monitor(args):
    """独立模式：启动新 Chrome 实例，使用保存的 cookie"""
    async with async_playwright() as p:
        temp_dir = tempfile.mkdtemp(prefix="token_monitor_")
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=temp_dir,
            headless=args.headless,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        cookies_loaded = await load_cookies(ctx, COOKIE_FILE)
        if not cookies_loaded:
            console.print("[red]❌ 未找到保存的 cookies，请先运行 --login 模式登录。[/red]")
            console.print(f"[yellow]运行: python3 {sys.argv[0]} --login[/yellow]")
            await ctx.close()
            return
        console.print("[green]✅ 浏览器已启动，开始监控...[/green]\n")
        iteration = 0
        while True:
            iteration += 1
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            console.print(f"[bold blue]━━━ [{timestamp}] 第 {iteration} 次抓取 ━━━[/bold blue]")
            result = await fetch_token_page(ctx, TARGET_URL, args)
            if not result["success"]:
                console.print(f"[red]❌ 抓取失败: {result.get('error', '未知错误')}[/red]")
                if "未登录" in result.get("error", ""):
                    console.print("[yellow]💡 Cookie 可能已过期，请重新运行 --login 登录[/yellow]")
                    break
            else:
                console.print(f"[green]✅ 加载成功 | 标题: {result.get('title', 'N/A')} | 耗时: {result['load_time']:.2f}s[/green]")
                display_data(result)
                if result.get("screenshot_path"):
                    console.print(f"[dim]📸 截图已保存: {result['screenshot_path']}[/dim]")
            if args.once:
                console.print("\n[green]单次抓取完成，退出。[/green]")
                break
            console.print(f"\n[dim]⏳ 等待 {args.interval} 秒后下次抓取... (Ctrl+C 退出)[/dim]\n")
            try:
                await asyncio.sleep(args.interval)
            except asyncio.CancelledError:
                break
        await ctx.close()


async def run_cdp_monitor(args):
    """CDP 模式：连接到已运行的 Chrome（推荐，复用登录态）"""
    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
            console.print(f"[green]✅ 已连接到正在运行的 Chrome（CDP 模式，端口 {CDP_PORT}）[/green]")
        except Exception as e:
            console.print(f"[red]❌ 无法连接到 Chrome CDP: {e}[/red]")
            console.print(f"[yellow]💡 请先以调试模式启动 Chrome:[/yellow]")
            console.print(f"[yellow]   /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port={CDP_PORT}[/yellow]")
            console.print(f"[yellow]   或运行: python3 {sys.argv[0]} --standalone[/yellow]")
            return
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        console.print("[green]✅ 浏览器已就绪，开始监控...[/green]\n")
        iteration = 0
        while True:
            iteration += 1
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            console.print(f"[bold blue]━━━ [{timestamp}] 第 {iteration} 次抓取 ━━━[/bold blue]")
            result = await fetch_token_page(ctx, TARGET_URL, args)
            if not result["success"]:
                console.print(f"[red]❌ 抓取失败: {result.get('error', '未知错误')}[/red]")
                if "未登录" in result.get("error", ""):
                    console.print("[yellow]💡 请先在 Chrome 中登录 OA 系统，再运行此脚本[/yellow]")
                    break
            else:
                console.print(f"[green]✅ 加载成功 | 标题: {result.get('title', 'N/A')} | 耗时: {result['load_time']:.2f}s[/green]")
                display_data(result)
                if result.get("screenshot_path"):
                    console.print(f"[dim]📸 截图已保存: {result['screenshot_path']}[/dim]")
            if args.once:
                console.print("\n[green]单次抓取完成，退出。[/green]")
                break
            console.print(f"\n[dim]⏳ 等待 {args.interval} 秒后下次抓取... (Ctrl+C 退出)[/dim]\n")
            try:
                await asyncio.sleep(args.interval)
            except asyncio.CancelledError:
                break
        await browser.close()


async def run_auto_monitor(args):
    """自动检测模式：先尝试 CDP，失败则提示用户"""
    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
            console.print(f"[green]✅ 已连接到正在运行的 Chrome（CDP 模式）[/green]")
            ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
            cdp_mode = True
        except Exception:
            console.print("[yellow]⚠ 未检测到 Chrome 远程调试端口[/yellow]")
            console.print("[yellow]💡 推荐使用 --cdp 模式（复用已登录的 Chrome）[/yellow]")
            console.print("[yellow]   或使用 --standalone 模式（独立运行）[/yellow]")
            console.print("[yellow]   查看帮助: python3 token_monitor.py --help[/yellow]")
            return
        console.print("[green]✅ 浏览器已就绪，开始监控...[/green]\n")
        iteration = 0
        while True:
            iteration += 1
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            console.print(f"[bold blue]━━━ [{timestamp}] 第 {iteration} 次抓取 ━━━[/bold blue]")
            result = await fetch_token_page(ctx, TARGET_URL, args)
            if not result["success"]:
                console.print(f"[red]❌ 抓取失败: {result.get('error', '未知错误')}[/red]")
                if "未登录" in result.get("error", ""):
                    console.print("[yellow]💡 请先在 Chrome 中登录 OA 系统，再运行此脚本[/yellow]")
                    break
            else:
                console.print(f"[green]✅ 加载成功 | 标题: {result.get('title', 'N/A')} | 耗时: {result['load_time']:.2f}s[/green]")
                display_data(result)
                if result.get("screenshot_path"):
                    console.print(f"[dim]📸 截图已保存: {result['screenshot_path']}[/dim]")
            if args.once:
                console.print("\n[green]单次抓取完成，退出。[/green]")
                break
            console.print(f"\n[dim]⏳ 等待 {args.interval} 秒后下次抓取... (Ctrl+C 退出)[/dim]\n")
            try:
                await asyncio.sleep(args.interval)
            except asyncio.CancelledError:
                break
        if cdp_mode:
            await browser.close()


def main():
    args = parse_args()
    console.print(Panel.fit(
        f"[bold cyan]🔍 Token 监控工具[/bold cyan]\n"
        f"目标: [underline]{TARGET_URL}[/underline]\n"
        f"间隔: {args.interval} 秒 | 模式: {'单次' if args.once else '循环'} | "
        f"{'无头' if args.headless else '有头'}模式\n"
        f"连接方式: {'CDP' if args.cdp else '独立' if args.standalone else '自动检测'}",
        border_style="cyan",
    ))
    try:
        if args.login:
            asyncio.run(run_login_flow(args))
        elif args.cdp:
            asyncio.run(run_cdp_monitor(args))
        elif args.standalone:
            asyncio.run(run_standalone_monitor(args))
        else:
            asyncio.run(run_auto_monitor(args))
    except KeyboardInterrupt:
        console.print("\n[yellow]👋 已退出。[/yellow]")


if __name__ == "__main__":
    main()