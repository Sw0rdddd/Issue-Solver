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

            try:
                lines = file_path.read_text(
                    encoding="utf-8",
                    errors="ignore",
                ).splitlines()
            except OSError:
                continue

            for line_number, line in enumerate(lines, start=1):
                compared_line = line if case_sensitive else line.lower()

                if search_query not in compared_line:
                    continue

                relative_path = file_path.relative_to(
                    repo_root
                ).as_posix()

                results.append(
                    f"{relative_path}:{line_number}: {line.strip()}"
                )

                if len(results) >= max_results:
                    return "\n".join(results)

    if not results:
        return (
            f"未找到文本：{query!r}，"
            f"搜索范围：{path}，"
            f"文件模式：{file_pattern}"
        )

    return "\n".join(results)


@tool
def search_symbol(repo_path: str,symbol: str,path: str = ".") -> str:
    """搜索 Python 文件中的类和函数定义。

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

    for file_path in target.rglob("*.py"):
        if any(
            part in {
                ".git",
                ".venv",
                "venv",
                ".conda",
                "__pycache__",
            }
            for part in file_path.parts
        ):
            continue

        try:
            source = file_path.read_text(
                encoding="utf-8",
                errors="ignore",
            )

            tree = ast.parse(source)

        except (SyntaxError, OSError):
            continue

        for node in ast.walk(tree):
            if not isinstance(
                node,
                (ast.ClassDef, ast.FunctionDef),
            ):
                continue

            if node.name != symbol:
                continue

            relative = file_path.relative_to(
                repo_root
            ).as_posix()

            node_type = (
                "class"
                if isinstance(node, ast.ClassDef)
                else "function"
            )

            results.append(
                f"{node_type} {symbol} "
                f"-> {relative}:{node.lineno}"
            )

    if not results:
        return f"未找到符号：{symbol}"

    return "\n".join(results)
