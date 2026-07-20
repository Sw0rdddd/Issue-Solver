import subprocess
from pathlib import Path


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
