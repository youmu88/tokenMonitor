#!/usr/bin/env python3
"""
Token Monitor - 版本号自动管理脚本
====================================
功能：
1. 自动检测修订规模（小补丁/功能演进），更新 3 位版本号
2. 构建检查（语法检查 + 模块导入检查）
3. git commit（自动生成简洁 msg）+ git push

用法:
    python3 version_manager.py              # 自动检测变更并更新版本
    python3 version_manager.py --patch      # 强制小补丁更新 (y++)
    python3 version_manager.py --minor      # 强制功能演进更新 (x++)
    python3 version_manager.py --major      # 强制大版本更新 (1++)
    python3 version_manager.py --check      # 仅检查，不更新
    python3 version_manager.py --status     # 查看当前版本状态
    python3 version_manager.py --dry-run    # 模拟运行（不实际修改/提交）
"""

import os
import sys
import re
import subprocess
import argparse
from pathlib import Path

# ============================================================
# 配置
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent
VERSION_FILE = PROJECT_ROOT / "VERSION"
PYTHON_FILES = sorted(PROJECT_ROOT.glob("*.py"))
SHELL_FILES = sorted(PROJECT_ROOT.glob("*.sh"))
ALL_SOURCE_FILES = PYTHON_FILES + SHELL_FILES

# 需要同步版本号的文件（含占位符 VERSION_PLACEHOLDER）
VERSION_SYNC_FILES = [
    PROJECT_ROOT / "setup.py",
    PROJECT_ROOT / "build_app.sh",
]

VERSION_PLACEHOLDER = "VERSION_PLACEHOLDER"


# ============================================================
# 版本号操作
# ============================================================
def read_version() -> str:
    """从 VERSION 文件读取当前版本号"""
    if not VERSION_FILE.exists():
        print(f"⚠️  VERSION 文件不存在，将创建并初始化为 1.0.0")
        return "1.0.0"
    ver = VERSION_FILE.read_text().strip()
    if not re.match(r"^\d+\.\d+\.\d+$", ver):
        print(f"⚠️  版本号格式异常: {ver!r}，重置为 1.0.0")
        return "1.0.0"
    return ver


def write_version(version: str):
    """写入 VERSION 文件"""
    VERSION_FILE.write_text(version.strip() + "\n")
    print(f"📝  VERSION 已更新: {version}")


def parse_version(version: str) -> tuple:
    """解析版本号为 (major, minor, patch)"""
    parts = version.strip().split(".")
    return int(parts[0]), int(parts[1]), int(parts[2])


def bump_version(current: str, bump_type: str) -> str:
    """根据 bump 类型生成新版本号"""
    major, minor, patch = parse_version(current)
    if bump_type == "major":
        return f"{major + 1}.0.0"
    elif bump_type == "minor":
        return f"{major}.{minor + 1}.0"
    elif bump_type == "patch":
        return f"{major}.{minor}.{patch + 1}"
    else:
        raise ValueError(f"未知的 bump 类型: {bump_type}")


# ============================================================
# 变更检测
# ============================================================
def detect_bump_type() -> str:
    """
    自动检测变更规模：
    - 新增 .py 文件 → minor（功能演进）
    - 修改 .py 文件（非注释/文档）→ minor
    - 仅修改注释/文档/配置 → patch
    - 仅修改 .sh / 其他 → patch
    """
    try:
        # 获取变更文件列表（排除 .venv/）
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10
        )
        staged = [f for f in result.stdout.strip().split("\n") if f and not f.startswith(".venv/")]

        result = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10
        )
        unstaged = [f for f in result.stdout.strip().split("\n") if f and not f.startswith(".venv/")]

        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10
        )
        untracked = [f for f in result.stdout.strip().split("\n") if f and not f.startswith(".venv/")]

        all_files = list(set(staged + unstaged + untracked))
        # 排除 version_manager.py 和 VERSION 自身
        all_files = [f for f in all_files if f not in ("version_manager.py", "VERSION")]

        if not all_files:
            print("ℹ️  没有检测到任何变更")
            return None

        # 检查是否有新增的 .py 文件
        new_py_files = [f for f in untracked if f.endswith(".py") and f not in ("version_manager.py", "VERSION")]
        if new_py_files:
            print(f"🔍  检测到新增 Python 文件: {', '.join(new_py_files)}")
            return "minor"

        # 检查是否有修改的 .py 文件（非仅注释）
        modified_py = [f for f in staged + unstaged if f.endswith(".py") and f not in ("version_manager.py", "VERSION")]
        if modified_py:
            # 检查是否只是注释/文档变更
            for pyf in modified_py:
                if os.path.exists(os.path.join(PROJECT_ROOT, pyf)):
                    result = subprocess.run(
                        ["git", "diff", "HEAD", "--", pyf],
                        capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10
                    )
                    diff_text = result.stdout
                    # 如果 diff 包含非注释/非空行的代码变更，视为 minor
                    code_lines = [l for l in diff_text.split("\n")
                                  if l.startswith("+") and not l.startswith("+++")
                                  and not l.startswith("+#") and l.strip() not in ("+", "")]
                    if code_lines:
                        return "minor"
            return "patch"

        # 其他变更（shell 脚本、配置文件等）→ patch
        return "patch"

    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "patch"


# ============================================================
# 构建检查
# ============================================================
def run_build_checks() -> bool:
    """执行构建检查：语法检查 + 模块导入检查"""
    print("\n🔍  === 构建检查 ===")
    all_ok = True

    # 1. Python 语法检查
    print("\n📄  检查 Python 语法...")
    for py_file in PYTHON_FILES:
        if py_file.name == "version_manager.py":
            continue  # 跳过自身（可能正在运行）
        try:
            subprocess.run(
                [sys.executable, "-c",
                 f"import py_compile; py_compile.compile(r'{py_file}', doraise=True)"],
                capture_output=True, text=True, check=True, timeout=30
            )
            print(f"  ✅ {py_file.name}")
        except subprocess.CalledProcessError as e:
            print(f"  ❌ {py_file.name}: 语法错误")
            print(f"     {e.stderr.strip()}")
            all_ok = False

    # 2. Shell 语法检查
    print("\n📄  检查 Shell 语法...")
    for sh_file in SHELL_FILES:
        try:
            subprocess.run(
                ["bash", "-n", str(sh_file)],
                capture_output=True, text=True, check=True, timeout=10
            )
            print(f"  ✅ {sh_file.name}")
        except subprocess.CalledProcessError as e:
            print(f"  ❌ {sh_file.name}: 语法错误")
            print(f"     {e.stderr.strip()}")
            all_ok = False

    # 3. 模块导入检查
    print("\n📦  检查模块导入...")
    core_modules = ["token_monitor_core", "token_db", "token_icon", "token_widget"]
    for mod in core_modules:
        try:
            subprocess.run(
                [sys.executable, "-c", f"import {mod}"],
                capture_output=True, text=True, check=True, timeout=30,
                cwd=PROJECT_ROOT
            )
            print(f"  ✅ {mod}")
        except subprocess.CalledProcessError as e:
            print(f"  ❌ {mod}: 导入失败")
            print(f"     {e.stderr.strip()}")
            all_ok = False

    if all_ok:
        print("\n✅  === 构建检查全部通过 ===")
    else:
        print("\n❌  === 构建检查发现错误 ===")

    return all_ok


# ============================================================
# Git 操作
# ============================================================
def get_git_diff_summary() -> tuple:
    """获取 git diff 摘要，返回 (last_commit_msg, changed_files_list)"""
    try:
        # 获取上次 commit message
        result = subprocess.run(
            ["git", "log", "-1", "--pretty=%s"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10
        )
        last_msg = result.stdout.strip()

        # 获取变更文件列表（排除 .venv/ 目录）
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10
        )
        staged = [f for f in result.stdout.strip().split("\n") if f and not f.startswith(".venv/")]

        result = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10
        )
        unstaged = [f for f in result.stdout.strip().split("\n") if f and not f.startswith(".venv/")]

        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10
        )
        untracked = [f for f in result.stdout.strip().split("\n") if f and not f.startswith(".venv/")]

        all_files = list(set(staged + unstaged + untracked))
        # 过滤掉 version_manager.py 和 VERSION 自身
        all_files = [f for f in all_files if f not in ("version_manager.py", "VERSION")]

        return last_msg, all_files

    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "", []


def generate_commit_message(bump_type: str, new_version: str, last_msg: str, changed_files: list) -> str:
    """自动生成简洁的 commit message"""
    # 只保留项目根目录的文件（排除 .venv/ 等）
    project_files = [f for f in changed_files if not f.startswith(".venv/") and not f.startswith("build/") and not f.startswith("dist/")]

    # 文件分类
    py_files = [f for f in project_files if f.endswith(".py")]
    sh_files = [f for f in project_files if f.endswith(".sh")]
    other_files = [f for f in project_files if not f.endswith(".py") and not f.endswith(".sh")]

    parts = []
    if py_files:
        parts.append(f"🐍 {', '.join(py_files)}")
    if sh_files:
        parts.append(f"📜 {', '.join(sh_files)}")
    if other_files:
        parts.append(f"📦 {', '.join(other_files)}")

    file_summary = " | ".join(parts) if parts else ""

    # 版本号前缀
    if bump_type == "major":
        prefix = "🔥"
    elif bump_type == "minor":
        prefix = "✨"
    else:
        prefix = "🔧"

    msg = f"{prefix} v{new_version}"
    if file_summary:
        msg += f" | {file_summary}"

    # 限制 commit message 长度（git 推荐 50 字以内，但允许稍长）
    if len(msg) > 200:
        # 只保留文件数量统计
        py_count = len(py_files)
        sh_count = len(sh_files)
        other_count = len(other_files)
        stats = []
        if py_count:
            stats.append(f"{py_count} py")
        if sh_count:
            stats.append(f"{sh_count} sh")
        if other_count:
            stats.append(f"{other_count} other")
        msg = f"{prefix} v{new_version} | {' + '.join(stats)} files"

    return msg


def git_commit_and_push(commit_msg: str, dry_run: bool = False) -> bool:
    """执行 git add / commit / push"""
    if dry_run:
        print(f"\n⏸️  模拟模式，跳过 git 操作")
        print(f"   commit: {commit_msg}")
        return True

    try:
        # git add 所有变更（排除 .venv/）
        print("\n📦  git add ...")
        subprocess.run(
            ["git", "add", "--all", "--", "."],
            capture_output=True, text=True, check=True, cwd=PROJECT_ROOT, timeout=30
        )
        # 取消 .venv/ 的暂存（确保不被提交）
        subprocess.run(
            ["git", "reset", "HEAD", "--", ".venv/"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10
        )

        # git commit
        print(f"💬  git commit -m \"{commit_msg}\"")
        result = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=30
        )
        if result.returncode != 0:
            print(f"⚠️  commit 失败: {result.stderr.strip()}")
            return False
        print(f"✅  commit 成功")

        # git push
        print("📤  git push ...")
        result = subprocess.run(
            ["git", "push"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=60
        )
        if result.returncode != 0:
            print(f"⚠️  push 失败: {result.stderr.strip()}")
            return False
        print(f"✅  push 成功")
        return True

    except subprocess.TimeoutExpired as e:
        print(f"⏰  超时: {e}")
        return False
    except subprocess.CalledProcessError as e:
        print(f"❌  git 操作失败: {e.stderr}")
        return False


# ============================================================
# 版本号同步
# ============================================================
def sync_version_to_files(version: str, dry_run: bool = False):
    """将版本号同步到 setup.py 和 build_app.sh"""
    for file_path in VERSION_SYNC_FILES:
        if not file_path.exists():
            print(f"⚠️  文件不存在，跳过: {file_path.name}")
            continue

        content = file_path.read_text()
        old_content = content

        if file_path.name == "setup.py":
            # 替换 APP_VERSION 赋值行
            content = re.sub(
                r'APP_VERSION\s*=\s*"[^"]*"',
                f'APP_VERSION = "{version}"',
                content
            )
        elif file_path.name == "build_app.sh":
            # 替换 VERSION="..." 行
            content = re.sub(
                r'VERSION="[^"]*"',
                f'VERSION="{version}"',
                content
            )

        if content != old_content:
            if dry_run:
                print(f"  ⏸️  {file_path.name}: 版本号将更新为 {version}")
            else:
                file_path.write_text(content)
                print(f"  ✅ {file_path.name}: APP_VERSION 已同步为 {version}")
        else:
            print(f"  ℹ️  {file_path.name}: 版本号无需变更")


# ============================================================
# 主入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="Token Monitor 版本号自动管理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 version_manager.py              自动检测变更并更新版本
  python3 version_manager.py --patch      强制小补丁更新 (y++)
  python3 version_manager.py --minor      强制功能演进更新 (x++)
  python3 version_manager.py --major      强制大版本更新 (1++)
  python3 version_manager.py --check      仅执行构建检查
  python3 version_manager.py --status     查看版本状态
  python3 version_manager.py --dry-run    模拟运行（不实际修改）
        """
    )
    parser.add_argument("--patch", action="store_true", help="强制小补丁更新 (y++)")
    parser.add_argument("--minor", action="store_true", help="强制功能演进更新 (x++)")
    parser.add_argument("--major", action="store_true", help="强制大版本更新 (1++)")
    parser.add_argument("--check", action="store_true", help="仅执行构建检查，不更新版本")
    parser.add_argument("--status", action="store_true", help="查看当前版本状态")
    parser.add_argument("--dry-run", action="store_true", help="模拟运行（不实际修改/提交）")
    parser.add_argument("--no-push", action="store_true", help="更新版本后不执行 git push")

    args = parser.parse_args()

    # --status 模式
    if args.status:
        show_status()
        return

    current_version = read_version()
    print(f"📌  当前版本: {current_version}")

    # --check 模式：仅检查
    if args.check:
        print("\n🔍  执行构建检查（仅检查模式）...")
        ok = run_build_checks()
        sys.exit(0 if ok else 1)

    # 确定 bump 类型
    if args.patch:
        bump_type = "patch"
    elif args.minor:
        bump_type = "minor"
    elif args.major:
        bump_type = "major"
    else:
        bump_type = detect_bump_type()
        if bump_type is None:
            print("ℹ️  无变更，无需更新版本号")
            return

    new_version = bump_version(current_version, bump_type)
    print(f"🆕  新版本号: {current_version} → {new_version} ({bump_type})")

    # --dry-run 模式
    if args.dry_run:
        print(f"\n⏸️  模拟模式（--dry-run），以下操作将被跳过:")
        print(f"  1. 同步版本号到 setup.py / build_app.sh")
        print(f"  2. 写入 VERSION 文件: {new_version}")
        print(f"  3. 执行构建检查")
        print(f"  4. git add / commit / push")
        print(f"\n✅  模拟完成，未做任何实际修改。")
        return

    # 1. 同步版本号到其他文件
    print("\n📋  同步版本号到项目文件...")
    sync_version_to_files(new_version)

    # 2. 写入 VERSION 文件
    write_version(new_version)

    # 3. 构建检查
    print("\n🔍  执行构建检查...")
    if not run_build_checks():
        print("\n❌  构建检查失败，中止版本更新。请修复错误后重试。")
        # 回滚 VERSION 文件
        write_version(current_version)
        sync_version_to_files(current_version)
        print("↩️  已回滚版本号至", current_version)
        sys.exit(1)

    # 4. 生成 commit message 并执行 git commit & push
    last_msg, changed_files = get_git_diff_summary()
    commit_msg = generate_commit_message(bump_type, new_version, last_msg, changed_files)
    print(f"\n💬  自动生成 commit message: {commit_msg}")

    if args.no_push:
        git_ok = git_commit_and_push(commit_msg, dry_run=False)
        # 手动跳过 push
        print("⏭️  --no-push 模式，跳过 git push")
        git_ok = True
    else:
        git_ok = git_commit_and_push(commit_msg, dry_run=False)

    if not git_ok and not args.no_push:
        print("\n⚠️  git push 失败，但版本已更新。")
        print("💡  可稍后手动执行: git push")

    # 完成
    print(f"\n✅  === 版本更新完成: {current_version} → {new_version} ===")
    print(f"   commit: {commit_msg}")
    if git_ok:
        print("   push:   ✅ 已推送至远程仓库")
    else:
        print("   push:   ⏭️  未推送（--no-push 或失败）")


def show_status():
    """显示当前版本状态"""
    version = read_version()
    major, minor, patch = parse_version(version)

    print(f"\n📊  Token Monitor 版本状态")
    print(f"========================================")
    print(f"  当前版本:  {version}")
    print(f"  主版本号:   {major}")
    print(f"  次版本号:   {minor}")
    print(f"  补丁号:     {patch}")
    print(f"  项目根目录: {PROJECT_ROOT}")
    print(f"  VERSION 文件: {VERSION_FILE}")
    print()

    # 显示 git 状态
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10
        )
        if result.stdout.strip():
            print("📂  未提交的变更:")
            for line in result.stdout.strip().split("\n"):
                if ".venv/" not in line:
                    print(f"  {line}")
        else:
            print("📂  工作区干净，无未提交变更")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    print()
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10
        )
        print("📜  最近提交:")
        print(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


if __name__ == "__main__":
    main()