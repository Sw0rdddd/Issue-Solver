import fnmatch
import os
import ast
from pathlib import Path

from langchain.tools import tool

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

MAX_SEARCH_FILE_BYTES = 1_048_576
MAX_SEARCH_FILES = 2_500
MAX_SYMBOL_RESULTS = 150


def _read_searchable_file(
        repo_root: Path,
        file_path: Path,
) -> tuple[Path, str] | None:
    """安全读取可搜索的仓库内 UTF-8 文本文件。"""

    try:
        if file_path.is_symlink():
            return None

        resolved_file = file_path.resolve()
        relative_file = resolved_file.relative_to(repo_root)

        if not resolved_file.is_file():
            return None

        if resolved_file.stat().st_size > MAX_SEARCH_FILE_BYTES:
            return None

        content = resolved_file.read_bytes()
    except (OSError, ValueError):
        return None

    if len(content) > MAX_SEARCH_FILE_BYTES or b"\x00" in content:
        return None

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None

    return relative_file, text


@tool
def search_text(repo_path: str, query: str, path: str = ".", file_pattern: str = "*", case_sensitive: bool = False,
                max_results: int = 50, ) -> str:
    """在仓库文件中搜索指定文本。

    Args:
        repo_path: Git 仓库根目录。
        query: 要搜索的文本，例如 "UserService"。
        path: 搜索范围，相对于仓库根目录，例如 "."、"src"。
        file_pattern: 文件名匹配模式，例如 "*.py"、"test_*.py"。
        case_sensitive: 是否区分大小写。
        max_results: 最大返回结果数量。
    """

    if not query.strip():
        return "错误：query 不能为空。"

    if max_results < 1:
        return "错误：max_results 必须大于等于 1。"

    if max_results > 200:
        return "错误：max_results 不能大于 200。"

    repo_root = Path(repo_path).resolve()
    target = (repo_root / path).resolve()

    # 防止通过 ../../ 搜索仓库外部
    try:
        relative_target = target.relative_to(repo_root)
    except ValueError:
        return "错误：禁止搜索仓库之外的路径。"
    if any(part in IGNORED_NAMES for part in relative_target.parts):
        return "错误：禁止搜索依赖或缓存目录。"

    if not target.exists():
        return f"错误：路径不存在：{path}"

    if not target.is_dir():
        return f"错误：该路径不是目录：{path}"

    search_query = query if case_sensitive else query.lower()
    results: list[str] = []
    scanned_files = 0
    scan_truncated = False

    for current_root, dir_names, file_names in os.walk(target):
        # 跳过依赖目录和缓存目录
        dir_names[:] = [
            name
            for name in dir_names
            if name not in IGNORED_NAMES
        ]

        for file_name in sorted(file_names):
            if not fnmatch.fnmatch(file_name, file_pattern):
                continue

            file_path = Path(current_root) / file_name

            if scanned_files >= MAX_SEARCH_FILES:
                scan_truncated = True
                break

            scanned_files += 1
            searchable_file = _read_searchable_file(repo_root, file_path)

            if searchable_file is None:
                continue

            relative_file, text = searchable_file

            for line_number, line in enumerate(text.splitlines(), start=1):
                compared_line = line if case_sensitive else line.lower()

                if search_query not in compared_line:
                    continue

                if len(results) >= max_results:
                    return "\n".join([
                        *results,
                        f"[结果已截断，当前显示前 {max_results} 条，"
                        f"请缩小 path 或 file_pattern]",
                    ])

                results.append(
                    f"{relative_file.as_posix()}:{line_number}: "
                    f"{line.strip()}"
                )

        if scan_truncated:
            break

    if scan_truncated:
        truncated_message = (
            f"[搜索已截断，当前仅扫描前 {MAX_SEARCH_FILES} 个候选文件，"
            f"请缩小 path 或 file_pattern]"
        )
        if results:
            return "\n".join([*results, truncated_message])
        return truncated_message

    if not results:
        return (
            f"未找到文本：{query!r}，"
            f"搜索范围：{path}，"
            f"文件模式：{file_pattern}"
        )

    return "\n".join(results)


@tool
def search_symbol(repo_path: str,symbol: str,path: str = ".") -> str:
    """搜索 Python 文件中的类、同步函数和异步函数定义。

    Args:
        repo_path: Git 仓库根目录。
        symbol: 要搜索的类名或函数名。
        path: 搜索范围。
    """

    repo_root = Path(repo_path).resolve()
    target = (repo_root / path).resolve()

    try:
        relative_target = target.relative_to(repo_root)
    except ValueError:
        return "错误：禁止访问仓库之外的路径。"
    if any(part in IGNORED_NAMES for part in relative_target.parts):
        return "错误：禁止搜索依赖或缓存目录。"

    results: list[str] = []
    scanned_files = 0
    scan_truncated = False

    for file_path in target.rglob("*.py"):
        try:
            relative_candidate = file_path.relative_to(repo_root)
        except ValueError:
            continue

        if any(part in IGNORED_NAMES for part in relative_candidate.parts):
            continue

        if scanned_files >= MAX_SEARCH_FILES:
            scan_truncated = True
            break

        scanned_files += 1
        searchable_file = _read_searchable_file(repo_root, file_path)

        if searchable_file is None:
            continue

        relative_file, source = searchable_file

        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(
                node,
                (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                continue

            if node.name != symbol:
                continue

            if len(results) >= MAX_SYMBOL_RESULTS:
                return "\n".join([
                    *results,
                    f"[结果已截断，当前显示前 {MAX_SYMBOL_RESULTS} 条]",
                ])

            node_type = (
                "class"
                if isinstance(node, ast.ClassDef)
                else "function"
            )

            results.append(
                f"{node_type} {symbol} "
                f"-> {relative_file.as_posix()}:{node.lineno}"
            )

    if scan_truncated:
        truncated_message = (
            f"[搜索已截断，当前仅扫描前 {MAX_SEARCH_FILES} 个候选文件，"
            f"请缩小 path]"
        )
        if results:
            return "\n".join([*results, truncated_message])
        return truncated_message

    if not results:
        return f"未找到符号：{symbol}"

    return "\n".join(results)
