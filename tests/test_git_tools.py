import subprocess
from pathlib import Path

from tools.git import git_show


def test_git_show_returns_commit_details_for_selected_path(git_repo: Path) -> None:
    (git_repo / "tracked.txt").write_text("updated\n", encoding="utf-8")
    (git_repo / "other.txt").write_text("other\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "tracked.txt", "other.txt"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Issue Solver Tests",
            "-c",
            "user.email=tests@example.com",
            "commit",
            "-m",
            "update tracked file",
        ],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    )

    result = git_show.invoke(
        {
            "repo_path": str(git_repo),
            "commit": "HEAD",
            "path": "tracked.txt",
        }
    )

    assert "update tracked file" in result
    assert "-initial" in result
    assert "+updated" in result
    assert "other.txt" not in result


def test_git_show_validates_path_and_output_limit(git_repo: Path) -> None:
    assert "禁止访问" in git_show.invoke(
        {
            "repo_path": str(git_repo),
            "commit": "HEAD",
            "path": "../outside.txt",
        }
    )
    assert "必须大于 0" in git_show.invoke(
        {
            "repo_path": str(git_repo),
            "commit": "HEAD",
            "max_chars": 0,
        }
    )
    assert "不能大于 100000" in git_show.invoke(
        {
            "repo_path": str(git_repo),
            "commit": "HEAD",
            "max_chars": 100_001,
        }
    )


def test_git_show_truncates_large_output(git_repo: Path) -> None:
    result = git_show.invoke(
        {
            "repo_path": str(git_repo),
            "commit": "HEAD",
            "max_chars": 10,
        }
    )

    assert result.startswith("commit ")
    assert "[输出已截断，请缩小 path 范围后重新查看]" in result


def test_git_show_reports_invalid_commit(git_repo: Path) -> None:
    result = git_show.invoke(
        {
            "repo_path": str(git_repo),
            "commit": "missing-commit",
        }
    )

    assert "错误：git show 执行失败" in result
