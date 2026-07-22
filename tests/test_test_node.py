import json
from pathlib import Path

from nodes import test as test_node_module
from schemas.coding_task import CodingTask
from schemas.environment_info import EnvironmentInfo
from schemas.failure import make_failure
from schemas.review_result import ReviewResult
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
            if status in {"ENVIRONMENT_ERROR", "TIMEOUT", "SAFETY_ERROR"}
            else 1
        ),
        duration=0.1,
        stdout_path=str(stdout),
        stderr_path=str(stderr),
        output_tail="[stdout] output",
        failure=(
            None
            if status == "PASSED"
            else make_failure(
                {
                    "FAILED": "SOLUTION",
                    "ENVIRONMENT_ERROR": "ENVIRONMENT",
                    "TIMEOUT": "LIMIT",
                    "SAFETY_ERROR": "SAFETY",
                }[status],
                "测试失败",
            )
        ),
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
        "coding_task": CodingTask(
            objective="修复示例",
            acceptance_criteria=["行为正确"],
            relevant_files=["sample.py"],
            root_cause="示例行为错误",
            allowed_scope=["sample.py", "tests/test_sample.py"],
            test_targets=["tests/test_sample.py::test_value"],
        ),
        "test_commands": ["pytest one", "pytest two"],
        "review_result": ReviewResult(
            verdict="APPROVE",
            issues=[],
            suggestions=[],
            remaining_risks=[],
        ),
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

    assert calls == [
        "pytest -q tests/test_sample.py::test_value",
        "pytest one",
    ]
    assert result["phase"] == "COORDINATE"
    assert result["cycle"] == 2
    assert [item.status for item in result["latest_test_results"]] == [
        "PASSED",
        "FAILED",
    ]
    artifact = json.loads(
        (tmp_path / "run" / "logs" / "test_result_r02.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(artifact["payload"]) == 2


def test_test_node_skips_regression_when_targeted_test_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def fake_execute(**kwargs):
        calls.append(kwargs["command"])
        return make_result(tmp_path, "FAILED")

    monkeypatch.setattr(test_node_module, "execute_test_command", fake_execute)
    monkeypatch.setattr(
        test_node_module,
        "worktree_fingerprint",
        lambda repo_path, base_commit: "unchanged",
    )

    result = test_node_module.build_test_node()(make_state(tmp_path))

    assert calls == ["pytest -q tests/test_sample.py::test_value"]
    assert result["latest_test_results"][0].status == "FAILED"


def test_test_node_runs_all_regression_commands_after_targeted_pass(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def fake_execute(**kwargs):
        calls.append(kwargs["command"])
        return make_result(tmp_path)

    monkeypatch.setattr(test_node_module, "execute_test_command", fake_execute)
    monkeypatch.setattr(
        test_node_module,
        "worktree_fingerprint",
        lambda repo_path, base_commit: "unchanged",
    )

    result = test_node_module.build_test_node()(make_state(tmp_path))

    assert calls == [
        "pytest -q tests/test_sample.py::test_value",
        "pytest one",
        "pytest two",
    ]
    assert all(
        item.status == "PASSED"
        for item in result["latest_test_results"]
    )
    assert result["next_action"] == "FINISH"
    assert result["phase"] == "FINALIZE"


def test_test_node_returns_to_coordinator_when_review_requests_changes(
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
        lambda **kwargs: make_result(tmp_path),
    )
    state = make_state(tmp_path)
    state["review_result"] = ReviewResult(
        verdict="REQUEST_CHANGES",
        issues=["缺少边界条件测试"],
        suggestions=[],
        remaining_risks=[],
    )

    result = test_node_module.build_test_node()(state)

    assert result["phase"] == "COORDINATE"
    assert result.get("next_action") is None


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
                "status": "SAFETY_ERROR",
                "exit_code": -1,
                "output_tail": message,
                "failure": make_failure("SAFETY", message),
            }
        )

    monkeypatch.setattr(test_node_module, "append_safety_error", fake_append)

    result = test_node_module.build_test_node()(make_state(tmp_path))

    assert result["rollback_required"] is True
    assert result["latest_test_results"][-1].status == "SAFETY_ERROR"


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
    assert result["failure"].type == "ENVIRONMENT"
    assert "修复目标仓库虚拟环境" in result["failure"].suggestion
    assert result.get("rollback_required") is None
