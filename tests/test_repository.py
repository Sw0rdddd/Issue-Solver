import subprocess
from pathlib import Path

import pytest

from services.repository import (
    find_repo_root,
    get_current_commit,
    get_repository_profile,
    is_worktree_clean,
)


def test_find_repo_root_from_nested_directory(git_repo: Path) -> None:
    nested = git_repo / "src" / "package"
    nested.mkdir(parents=True)

    assert find_repo_root(nested).resolve() == git_repo.resolve()


def test_get_current_commit_returns_head(git_repo: Path) -> None:
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    assert get_current_commit(git_repo) == expected


def test_worktree_cleanliness_includes_untracked_files(git_repo: Path) -> None:
    assert is_worktree_clean(git_repo) is True

    (git_repo / "untracked.txt").write_text("new\n", encoding="utf-8")

    assert is_worktree_clean(git_repo) is False


def test_repository_profile_counts_all_tracked_file_types(
    git_repo: Path,
) -> None:
    profile = get_repository_profile(git_repo)

    assert profile.tracked_file_count == 1
    assert profile.tracked_file_bytes == (git_repo / "tracked.txt").stat().st_size
    assert profile.file_counts_by_extension == {".txt": 1}


def test_find_repo_root_rejects_non_git_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不属于 Git 仓库"):
        find_repo_root(tmp_path)


def test_get_current_commit_rejects_empty_repository(tmp_path: Path) -> None:
    repo_path = tmp_path / "empty-repo"
    repo_path.mkdir()
    subprocess.run(
        ["git", "init"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )

    with pytest.raises(RuntimeError, match="无法获取当前 Commit"):
        get_current_commit(repo_path)
