import json
from pathlib import Path

from nodes import test as test_node_module
from schemas.environment_info import EnvironmentInfo
from schemas.test_result import TestResult as ExecutionResult


def make_result(tmp_path: Path, status: str = "PASSED") -> ExecutionResult:
    stdout = tmp_path / "stdout.log"
    stderr = tmp_path / "stderr.log"
    stdout.write_text("output\n", encoding="utf-8")
    stderr.write_text("", encoding="utf-8")
    return ExecutionResult(
        command="pytest -q",
        resolved_command=["C:/repo/.venv/Scripts/python.exe", "-m", "pytest", "-q"],
        cwd="C:/repo",
        python_executable="C:/repo/.venv/Scripts/python.exe",
        status=status,
        exit_code=(
            0
            if status == "PASSED"
            else -1
            if status in {"ENVIRONMENT_ERROR", "TIMEOUT"}
            else 1
        ),
        duration=0.1,
        stdout_path=str(stdout),
        stderr_path=str(stderr),
        output_tail="[stdout] output",
    )


def make_state(tmp_path: Path) -> dict:
    return {
        "run_id": "run_test",
        "phase": "TEST",
        "status": "RUNNING",
        "cycle": 1,
        "repair_round": 2,
        "repo_path": str(tmp_path),
        "base_commit": "abc123",
        "run_dir": str(tmp_path / "run"),
        "issue_input": "test",
        "test_commands": ["pytest one", "pytest two"],
        "environment": EnvironmentInfo(
            kind="VENV",
            root_path="C:/repo/.venv",
            python_executable="C:/repo/.venv/Scripts/python.exe",
            pytest_version="pytest 9",
            source=".venv",
        ),
        "test_timeout": 30,
        "test_tail_lines": 20,
    }


def test_test_node_aggregates_round_results_and_stops_on_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def fake_execute(**kwargs):
        calls.append(kwargs["command"])
        return make_result(
            tmp_path,
            "PASSED" if len(calls) == 1 else "FAILED",
        )

    monkeypatch.setattr(test_node_module, "execute_test_command", fake_execute)
    monkeypatch.setattr(
        test_node_module,
        "worktree_fingerprint",
        lambda repo_path, base_commit: "unchanged",
    )

    result = test_node_module.build_test_node()(make_state(tmp_path))

    assert calls == ["pytest one", "pytest two"]
    assert result["phase"] == "COORDINATE"
    assert result["cycle"] == 2
    assert [item.status for item in result["latest_test_results"]] == [
        "PASSED",
        "FAILED",
    ]
    artifact = json.loads(
        (tmp_path / "run" / "test_result_r02.json").read_text(encoding="utf-8")
    )
    assert len(artifact["payload"]) == 2


def test_test_node_marks_worktree_mutation_for_rollback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fingerprints = iter(["before", "after"])
    monkeypatch.setattr(
        test_node_module,
        "worktree_fingerprint",
        lambda repo_path, base_commit: next(fingerprints),
    )
    monkeypatch.setattr(
        test_node_module,
        "execute_test_command",
        lambda **kwargs: make_result(tmp_path),
    )

    def fake_append(result, message, tail_lines):
        return result.model_copy(
            update={
                "status": "ENVIRONMENT_ERROR",
                "exit_code": -1,
                "output_tail": message,
            }
        )

    monkeypatch.setattr(test_node_module, "append_environment_error", fake_append)

    result = test_node_module.build_test_node()(make_state(tmp_path))

    assert result["rollback_required"] is True
    assert result["latest_test_results"][-1].status == "ENVIRONMENT_ERROR"


def test_test_node_stops_without_coordinator_on_environment_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        test_node_module,
        "worktree_fingerprint",
        lambda repo_path, base_commit: "unchanged",
    )
    monkeypatch.setattr(
        test_node_module,
        "execute_test_command",
        lambda **kwargs: make_result(tmp_path, "ENVIRONMENT_ERROR"),
    )

    result = test_node_module.build_test_node()(make_state(tmp_path))

    assert result["status"] == "FAILED"
    assert result["phase"] == "TEST"
    assert "修复目标仓库虚拟环境" in result["error"]
    assert result.get("rollback_required") is None
