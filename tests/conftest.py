import subprocess
from pathlib import Path

import pytest


def run_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    run_git(repo_path, "init")
    (repo_path / "tracked.txt").write_text("initial\n", encoding="utf-8")
    run_git(repo_path, "add", "tracked.txt")
    run_git(
        repo_path,
        "-c",
        "user.name=Issue Solver Tests",
        "-c",
        "user.email=tests@example.com",
        "commit",
        "-m",
        "initial commit",
    )

    return repo_path
