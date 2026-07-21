from pathlib import Path

import pytest

from nodes import initialize
from schemas.environment_info import EnvironmentInfo


ENVIRONMENT = EnvironmentInfo(
    kind="VENV",
    root_path="C:/repo/.venv",
    python_executable="C:/repo/.venv/Scripts/python.exe",
    pytest_version="pytest 9.1.1",
    source=".venv",
)


def test_initialize_node_returns_repository_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(initialize, "find_repo_root", lambda path: repo_root)
    monkeypatch.setattr(initialize, "is_worktree_clean", lambda path: True)
    monkeypatch.setattr(initialize, "get_current_commit", lambda path: "abc123")
    monkeypatch.setattr(initialize, "detect_project_type", lambda path: "python")
    monkeypatch.setattr(
        initialize,
        "detect_test_commands",
        lambda path: ["pytest -q"],
    )

    result = initialize.initialize_node(
        {"repo_path": str(tmp_path / "input"), "environment": ENVIRONMENT}
    )

    assert result == {
        "repo_path": str(repo_root),
        "base_commit": "abc123",
        "project_type": "python",
        "test_commands": ["pytest -q"],
        "environment": ENVIRONMENT,
        "phase": "PARSE_ISSUE",
    }


def test_initialize_node_rejects_dirty_worktree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(initialize, "find_repo_root", lambda path: tmp_path)
    monkeypatch.setattr(initialize, "is_worktree_clean", lambda path: False)

    result = initialize.initialize_node(
        {"repo_path": str(tmp_path), "environment": ENVIRONMENT}
    )

    assert result["status"] == "FAILED"
    assert "未提交修改" in result["failure"].message
    assert result["failure"].type == "SAFETY"


def test_initialize_node_rejects_unknown_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(initialize, "find_repo_root", lambda path: tmp_path)
    monkeypatch.setattr(initialize, "is_worktree_clean", lambda path: True)
    monkeypatch.setattr(initialize, "get_current_commit", lambda path: "abc123")
    monkeypatch.setattr(initialize, "detect_project_type", lambda path: "unknown")

    result = initialize.initialize_node(
        {"repo_path": str(tmp_path), "environment": ENVIRONMENT}
    )

    assert result["status"] == "FAILED"
    assert "无法识别项目类型" in result["failure"].message
    assert result["failure"].type == "ENVIRONMENT"


def test_initialize_node_returns_repository_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_find_repo_root(path: Path) -> Path:
        raise ValueError("不是 Git 仓库")

    monkeypatch.setattr(initialize, "find_repo_root", fail_find_repo_root)

    result = initialize.initialize_node({"repo_path": str(tmp_path)})

    assert result["status"] == "FAILED"
    assert result["failure"].type == "ENVIRONMENT"
    assert result["failure"].message == "不是 Git 仓库"
