import fnmatch
import os
from pathlib import Path

from langchain.tools import tool

from schemas.failure import FailureType, format_failure_for_agent, make_failure

IGNORED_NAMES = {
    ".git",
    ".venv",
    "venv",
    ".conda",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
}


def _failure(failure_type: FailureType, message: str) -> str:
    return format_failure_for_agent(make_failure(failure_type, message))


@tool
def list_files(
        repo_path: str,
        path: str = ".",
        max_depth: int = 1,
        max_entries: int = 500,
) -> str:
    """列出仓库指定目录下的文件和子目录。

    Args:
        repo_path: Git 仓库根目录。
        path: 相对于仓库根目录的目录，例如 "."、"src"、"tests"。
        max_depth: 向下遍历的最大深度，0 表示只查看当前目录。
        max_entries: 最大返回条目数，允许 1 到 2000。
    """

    if max_depth < 0:
        return _failure("INPUT", "max_depth 不能小于 0。")

    if max_depth > 5:
        return _failure("INPUT", "max_depth 不能大于 5。")

    if max_entries < 1:
        return _failure("INPUT", "max_entries 必须大于等于 1。")

    if max_entries > 2_000:
        return _failure("INPUT", "max_entries 不能大于 2000。")

    repo_root = Path(repo_path).resolve()
    target = (repo_root / path).resolve()

    # 防止通过 ../../ 访问仓库外部
    try:
        relative_target = target.relative_to(repo_root)
    except ValueError:
        return _failure("SAFETY", "禁止访问仓库之外的路径。")
    if any(part in IGNORED_NAMES for part in relative_target.parts):
        return _failure("SAFETY", "禁止访问依赖或缓存目录。")

    if not target.exists():
        return _failure("INPUT", f"路径不存在：{path}")

    if not target.is_dir():
        return _failure("INPUT", f"该路径不是目录：{path}")

    results: list[str] = []

    for current_root, dir_names, file_names in os.walk(target):
        current_path = Path(current_root)

        current_depth = len(
            current_path.relative_to(target).parts
        )

        # 忽略不需要探索的目录
        dir_names[:] = sorted(
            name
            for name in dir_names
            if name not in IGNORED_NAMES
        )

        file_names.sort()

        # 记录当前目录中的子目录
        for dir_name in dir_names:
            if len(results) >= max_entries:
                return "\n".join([
                    *results,
                    f"[目录结果已截断，共显示前 {max_entries} 项，"
                    f"请缩小 path 或 max_depth]",
                ])

            dir_path = current_path / dir_name
            relative_path = dir_path.relative_to(repo_root).as_posix()

            results.append(f"[DIR] {relative_path}/")

        # 记录当前目录中的文件
        for file_name in file_names:
            if len(results) >= max_entries:
                return "\n".join([
                    *results,
                    f"[目录结果已截断，共显示前 {max_entries} 项，"
                    f"请缩小 path 或 max_depth]",
                ])

            file_path = current_path / file_name
            relative_path = file_path.relative_to(repo_root).as_posix()

            results.append(f"[FILE] {relative_path}")

        # 达到最大深度，不再进入下一层目录
        if current_depth >= max_depth:
            dir_names.clear()

    if not results:
        return f"目录为空：{path}"

    return "\n".join(results)


@tool
def read_file(repo_path: str, path: str, start_line: int = 1, end_line: int = 200) -> str:
    """读取仓库中指定文件的部分内容。

    Args:
        repo_path: Git 仓库根目录。
        path: 相对于仓库根目录的文件路径，例如 "src/main.py"。
        start_line: 开始行号，从 1 开始。
        end_line: 结束行号，包含该行。
    """

    if start_line < 1:
        return _failure("INPUT", "start_line 必须大于等于 1。")

    if end_line < start_line:
        return _failure("INPUT", "end_line 不能小于 start_line。")

    if end_line - start_line + 1 > 500:
        return _failure("INPUT", "单次最多读取 500 行。")

    repo_root = Path(repo_path).resolve()
    target = (repo_root / path).resolve()

    # 防止通过 ../../ 读取仓库外的文件
    try:
        relative_target = target.relative_to(repo_root)
    except ValueError:
        return _failure("SAFETY", "禁止读取仓库之外的文件。")
    if any(part in IGNORED_NAMES for part in relative_target.parts):
        return _failure("SAFETY", "禁止读取依赖或缓存目录。")

    if not target.exists():
        return _failure("INPUT", f"文件不存在：{path}")

    if not target.is_file():
        return _failure("INPUT", f"该路径不是文件：{path}")

    try:
        lines = target.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()
    except OSError as exc:
        return _failure("ENVIRONMENT", f"读取文件失败：{exc}")

    if not lines:
        return f"文件为空：{path}"

    if start_line > len(lines):
        return _failure(
            "INPUT",
            f"start_line 超出文件范围，该文件共 {len(lines)} 行。",
        )

    actual_end = min(end_line, len(lines))

    selected_lines = lines[start_line - 1:actual_end]

    result = [
        f"{line_number:>4} | {content}"
        for line_number, content in enumerate(
            selected_lines,
            start=start_line,
        )
    ]

    return "\n".join([
        f"File: {relative_target.as_posix()}",
        f"Lines: {start_line}-{actual_end} of {len(lines)}",
        "",
        *result,
    ])


