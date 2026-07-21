import subprocess
from pathlib import Path

from langchain.tools import tool

from schemas.failure import FailureType, format_failure_for_agent, make_failure


def _failure(failure_type: FailureType, message: str) -> str:
    return format_failure_for_agent(make_failure(failure_type, message))


@tool
def git_diff(repo_path: str,base_commit: str = "HEAD",path: str = ".",max_chars: int = 20_000) -> str:
    """查看仓库当前代码相对于指定 Commit 的差异。

    Args:
        repo_path: Git 仓库根目录。
        base_commit: 对比的基础 Commit，默认是 HEAD。
        path: 相对于仓库根目录的文件或目录，例如 "."、"src"。
        max_chars: 最大返回字符数，防止 Diff 过大。
    """

    if max_chars < 1:
        return _failure("INPUT", "max_chars 必须大于 0。")

    if max_chars > 100_000:
        return _failure("INPUT", "max_chars 不能大于 100000。")

    repo_root = Path(repo_path).resolve()
    target = (repo_root / path).resolve()

    try:
        relative_path = target.relative_to(repo_root)
    except ValueError:
        return _failure("SAFETY", "禁止访问仓库之外的路径。")

    if not repo_root.is_dir():
        return _failure("INPUT", f"仓库路径不存在：{repo_path}")

    command = [
        "git",
        "diff",
        "--no-ext-diff",
        base_commit,
        "--",
        relative_path.as_posix(),
    ]

    try:
        result = subprocess.run(
            command,
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return _failure("ENVIRONMENT", "未找到 Git，请先安装并配置 Git。")
    except subprocess.TimeoutExpired:
        return _failure("LIMIT", "git diff 执行超时。")

    if result.returncode != 0:
        error = result.stderr.strip() or "未知 Git 错误"
        return _failure("ENVIRONMENT", f"git diff 执行失败：{error}")

    diff = result.stdout

    if not diff.strip():
        return "当前没有检测到代码修改。"

    if len(diff) > max_chars:
        diff = diff[:max_chars]
        diff += "\n\n[输出已截断，请缩小 path 范围后重新查看]"

    return diff


@tool
def git_log(repo_path: str,path: str = ".",limit: int = 10) -> str:
    """查看仓库指定路径相关的最近 Git 提交记录。

    Args:
        repo_path: Git 仓库根目录。
        path: 相对于仓库根目录的文件或目录，例如 "."、"src/main.py"。
        limit: 最大返回提交数量。
    """

    if limit < 1:
        return _failure("INPUT", "limit 必须大于等于 1。")

    if limit > 50:
        return _failure("INPUT", "limit 不能大于 50。")

    repo_root = Path(repo_path).resolve()
    target = (repo_root / path).resolve()

    try:
        relative_path = target.relative_to(repo_root)
    except ValueError:
        return _failure("SAFETY", "禁止访问仓库之外的路径。")

    if not repo_root.is_dir():
        return _failure("INPUT", f"仓库路径不存在：{repo_path}")

    command = [
        "git",
        "log",
        f"--max-count={limit}",
        "--date=short",
        "--pretty=format:%h | %ad | %an | %s",
        "--",
        relative_path.as_posix(),
    ]

    try:
        result = subprocess.run(
            command,
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return _failure("ENVIRONMENT", "未找到 Git，请先安装并配置 Git。")
    except subprocess.TimeoutExpired:
        return _failure("LIMIT", "git log 执行超时。")

    if result.returncode != 0:
        error = result.stderr.strip() or "未知 Git 错误"
        return _failure("ENVIRONMENT", f"git log 执行失败：{error}")

    output = result.stdout.strip()

    if not output:
        return f"没有找到与路径 {path!r} 相关的提交记录。"

    return output


@tool
def git_show(
        repo_path: str,
        commit: str,
        path: str = ".",
        max_chars: int = 20_000,
) -> str:
    """查看指定提交对仓库路径所做的修改。

    Args:
        repo_path: Git 仓库根目录。
        commit: 要查看的 Commit、分支或标签。
        path: 相对于仓库根目录的文件或目录，例如 "."、"src/main.py"。
        max_chars: 最大返回字符数，防止输出过大。
    """

    if max_chars < 1:
        return _failure("INPUT", "max_chars 必须大于 0。")

    if max_chars > 100_000:
        return _failure("INPUT", "max_chars 不能大于 100000。")

    repo_root = Path(repo_path).resolve()
    target = (repo_root / path).resolve()

    try:
        relative_path = target.relative_to(repo_root)
    except ValueError:
        return _failure("SAFETY", "禁止访问仓库之外的路径。")

    if not repo_root.is_dir():
        return _failure("INPUT", f"仓库路径不存在：{repo_path}")

    command = [
        "git",
        "show",
        "--no-ext-diff",
        "--format=fuller",
        "--end-of-options",
        commit,
        "--",
        relative_path.as_posix(),
    ]

    try:
        result = subprocess.run(
            command,
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return _failure("ENVIRONMENT", "未找到 Git，请先安装并配置 Git。")
    except subprocess.TimeoutExpired:
        return _failure("LIMIT", "git show 执行超时。")

    if result.returncode != 0:
        error = result.stderr.strip() or "未知 Git 错误"
        return _failure("ENVIRONMENT", f"git show 执行失败：{error}")

    output = result.stdout

    if not output.strip():
        return f"提交 {commit!r} 没有修改路径 {path!r}。"

    if len(output) > max_chars:
        output = output[:max_chars]
        output += "\n\n[输出已截断，请缩小 path 范围后重新查看]"

    return output
