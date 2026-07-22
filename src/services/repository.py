import subprocess
from pathlib import Path

from schemas.repository_profile import RepositoryProfile


def find_repo_root(repo_path: Path) -> Path:
    """返回指定路径所在的 Git 仓库根目录。"""

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=repo_path.resolve(),
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"当前路径不属于 Git 仓库：{repo_path}") from exc

    return Path(result.stdout.strip())


def get_current_commit(repo_path: Path) -> str:
    """获取当前 Git commit。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path.resolve(),
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("无法获取当前 Commit，仓库可能还没有任何提交。") from exc

    return result.stdout.strip()


def is_worktree_clean(repo_path: Path) -> bool:
    """检查 Git 工作区是否没有未提交和未跟踪的文件。"""

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_path.resolve(),
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("无法检查 Git 工作区状态。") from exc

    return not result.stdout.strip()


def get_repository_profile(repo_path: Path) -> RepositoryProfile:
    """统计目标 Git 仓库的全部跟踪常规文件画像。"""

    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=repo_path.resolve(),
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("无法读取 Git 跟踪文件列表。") from exc

    tracked_files = [
        Path(item)
        for item in result.stdout.decode("utf-8").split("\0")
        if item
    ]
    file_count = 0
    total_bytes = 0
    extension_counts: dict[str, int] = {}
    for relative_path in tracked_files:
        target = repo_path / relative_path
        if not target.is_file():
            continue
        file_count += 1
        total_bytes += target.stat().st_size
        extension = relative_path.suffix.lower() or "<none>"
        extension_counts[extension] = extension_counts.get(extension, 0) + 1

    return RepositoryProfile(
        tracked_file_count=file_count,
        tracked_file_bytes=total_bytes,
        file_counts_by_extension=dict(sorted(extension_counts.items())),
    )
