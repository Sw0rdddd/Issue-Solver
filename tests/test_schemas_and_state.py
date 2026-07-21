from typing import get_args

import pytest
from pydantic import ValidationError

from graph.state import NextAction, Phase, ResolverState, RunStatus
from schemas.coding_result import CodingResult
from schemas.coding_task import CodingTask
from schemas.explore_report import ExploreReport
from schemas.failure import FailureInfo, make_failure
from schemas.issue_specification import IssueSpec
from schemas.review_result import ReviewResult
from schemas.test_result import TestResult as ExecutionResult


def test_issue_spec_defaults_and_json_serialization() -> None:
    issue = IssueSpec(title="空结果异常", body="查询没有数据时返回 500")

    assert issue.expected_behavior == ""
    assert issue.actual_behavior == ""
    assert issue.acceptance_criteria == []
    assert "空结果异常" in issue.model_dump_json()


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            ExploreReport,
            {
                "focus": "定位异常",
                "relevant_files": ["app.py"],
                "relevant_symbols": ["handle_request"],
                "findings": ["空值未经处理"],
                "root_cause": "直接遍历 None",
                "test_targets": ["test_app.py"],
                "unknowns": [],
            },
        ),
        (
            CodingTask,
            {
                "objective": "修复空值处理",
                "acceptance_criteria": ["返回空列表"],
                "relevant_files": ["app.py"],
                "root_cause": "返回值可能为 None",
                "allowed_scope": ["app.py"],
                "test_targets": ["tests/test_app.py"],
            },
        ),
        (
            CodingResult,
            {
                "success": True,
                "changed_files": ["app.py"],
                "summary": "增加空值处理",
                "diff_path": None,
                "validation": ["已调用 inspect_changes 检查累计差异"],
                "remaining_risks": [],
                "failure": None,
            },
        ),
        (
            ReviewResult,
            {
                "verdict": "APPROVE",
                "issues": [],
                "suggestions": [],
                "remaining_risks": [],
            },
        ),
        (
            ExecutionResult,
            {
                "command": "pytest -q",
                "resolved_command": ["python", "-m", "pytest", "-q"],
                "cwd": "C:/repo",
                "python_executable": "C:/repo/.venv/Scripts/python.exe",
                "status": "PASSED",
                "exit_code": 0,
                "duration": 0.5,
                "stdout_path": "stdout.log",
                "stderr_path": "stderr.log",
                "output_tail": "[stdout] 1 passed",
                "failure": None,
            },
        ),
    ],
)
def test_schema_accepts_complete_payload(model: type, payload: dict) -> None:
    value = model.model_validate(payload)

    assert value.model_dump() == payload


@pytest.mark.parametrize(
    "model",
    [
        IssueSpec,
        ExploreReport,
        CodingTask,
        CodingResult,
        ReviewResult,
        ExecutionResult,
    ],
)
def test_schema_rejects_missing_required_fields(model: type) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({})


@pytest.mark.parametrize(
    "failure_type",
    [
        "INPUT",
        "ENVIRONMENT",
        "MODEL",
        "SOLUTION",
        "SAFETY",
        "LIMIT",
        "INTERNAL",
    ],
)
def test_failure_info_accepts_supported_types(failure_type: str) -> None:
    failure = FailureInfo(
        type=failure_type,
        message="具体原因",
        suggestion="下一步建议",
    )

    assert failure.type == failure_type


def test_failure_info_rejects_unknown_type_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        FailureInfo.model_validate(
            {
                "type": "UNKNOWN",
                "message": "原因",
                "suggestion": "建议",
                "code": "legacy",
            }
        )


def test_coding_result_requires_failure_only_when_unsuccessful() -> None:
    with pytest.raises(ValidationError):
        CodingResult(
            success=False,
            changed_files=[],
            summary="未完成",
            diff_path=None,
            validation=[],
            remaining_risks=[],
        )

    result = CodingResult(
        success=False,
        changed_files=[],
        summary="未完成",
        diff_path=None,
        validation=[],
        remaining_risks=[],
        failure=make_failure("SOLUTION", "方案无效"),
    )
    assert result.failure.type == "SOLUTION"


def test_coding_result_rejects_legacy_string_fields() -> None:
    with pytest.raises(ValidationError):
        CodingResult(
            success="true",
            changed_files="app.py",
            summary="增加空值处理",
            diff_path="diff.patch",
            validation="pytest -q",
            remaining_risks="无",
        )


def test_coding_task_rejects_legacy_string_lists() -> None:
    with pytest.raises(ValidationError):
        CodingTask(
            objective="修复空值处理",
            acceptance_criteria="返回空列表",
            relevant_files="app.py",
            root_cause="返回值可能为 None",
            allowed_scope="app.py, tests/test_app.py",
            test_targets="tests/test_app.py",
        )


@pytest.mark.parametrize(
    "invalid_path",
    [".", "../app.py", "/app.py", "C:/repo/app.py"],
)
def test_coding_task_rejects_non_relative_scope(invalid_path: str) -> None:
    with pytest.raises(ValidationError):
        CodingTask(
            objective="修复空值处理",
            acceptance_criteria=["返回空列表"],
            relevant_files=["app.py"],
            root_cause="返回值可能为 None",
            allowed_scope=[invalid_path],
            test_targets=["tests/test_app.py"],
        )


def test_coding_task_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        CodingTask(
            objective="修复空值处理",
            acceptance_criteria=["返回空列表"],
            relevant_files=["app.py"],
            root_cause="返回值可能为 None",
            allowed_scope=["app.py"],
            test_targets=["tests/test_app.py"],
            shell_command="rm -rf .",
        )


@pytest.mark.parametrize(
    "invalid_target",
    [
        "",
        "tests",
        "../tests/test_app.py",
        "/tests/test_app.py",
        "C:/repo/tests/test_app.py",
        "pytest -q tests/test_app.py",
        "tests/test_app.py::",
        "tests/test_app.py;whoami",
    ],
)
def test_coding_task_rejects_invalid_test_targets(
    invalid_target: str,
) -> None:
    with pytest.raises(ValidationError):
        CodingTask(
            objective="修复空值处理",
            acceptance_criteria=["返回空列表"],
            relevant_files=["app.py"],
            root_cause="返回值可能为 None",
            allowed_scope=["app.py"],
            test_targets=[invalid_target],
        )


def test_coding_task_normalizes_pytest_node_ids() -> None:
    task = CodingTask(
        objective="修复空值处理",
        acceptance_criteria=["返回空列表"],
        relevant_files=["app.py"],
        root_cause="返回值可能为 None",
        allowed_scope=["app.py"],
        test_targets=["tests\\test_app.py::TestQuery::test_empty"],
    )

    assert task.test_targets == [
        "tests/test_app.py::TestQuery::test_empty"
    ]


@pytest.mark.parametrize(
    "test_targets",
    [
        ["tests/test_app.py", "tests/test_app.py"],
        [f"tests/test_{index}.py" for index in range(11)],
    ],
)
def test_coding_task_rejects_duplicate_or_excessive_test_targets(
    test_targets: list[str],
) -> None:
    with pytest.raises(ValidationError):
        CodingTask(
            objective="修复空值处理",
            acceptance_criteria=["返回空列表"],
            relevant_files=["app.py"],
            root_cause="返回值可能为 None",
            allowed_scope=["app.py"],
            test_targets=test_targets,
        )


def test_review_result_rejects_unknown_verdict() -> None:
    with pytest.raises(ValidationError):
        ReviewResult(
            verdict="UNKNOWN",
            issues=[],
            suggestions=[],
            remaining_risks=[],
        )


def test_review_result_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ReviewResult(
            verdict="APPROVE",
            issues=[],
            suggestions=[],
            remaining_risks=[],
            score=100,
        )


@pytest.mark.parametrize("field", ["issues", "suggestions", "remaining_risks"])
def test_review_result_rejects_blank_list_items(field: str) -> None:
    payload = {
        "verdict": "APPROVE",
        "issues": [],
        "suggestions": [],
        "remaining_risks": [],
    }
    payload[field] = ["   "]
    if field == "issues":
        payload["verdict"] = "REQUEST_CHANGES"

    with pytest.raises(ValidationError):
        ReviewResult.model_validate(payload)


def test_review_result_rejects_approve_with_issues() -> None:
    with pytest.raises(ValidationError):
        ReviewResult(
            verdict="APPROVE",
            issues=["search.py 仍然区分大小写"],
            suggestions=[],
            remaining_risks=[],
        )


def test_review_result_rejects_request_changes_without_issues() -> None:
    with pytest.raises(ValidationError):
        ReviewResult(
            verdict="REQUEST_CHANGES",
            issues=[],
            suggestions=["补充大小写测试"],
            remaining_risks=[],
        )


def test_review_result_accepts_request_changes_with_issue() -> None:
    result = ReviewResult(
        verdict="REQUEST_CHANGES",
        issues=["search.py 未归一化标题大小写"],
        suggestions=["比较前对查询和标题调用 casefold"],
        remaining_risks=[],
    )

    assert result.verdict == "REQUEST_CHANGES"


def test_test_result_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        ExecutionResult(
            command="pytest -q",
            resolved_command=["C:/repo/.venv/Scripts/python.exe", "-m", "pytest", "-q"],
            cwd="C:/repo",
            python_executable="C:/repo/.venv/Scripts/python.exe",
            status="UNKNOWN",
            exit_code=1,
            duration=0.1,
            stdout_path="stdout.log",
            stderr_path="stderr.log",
            output_tail="",
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "command": "pytest -q",
            "resolved_command": ["python", "-m", "pytest", "-q"],
            "cwd": "C:/repo",
            "python_executable": "C:/repo/.venv/Scripts/python.exe",
            "status": "PASSED",
            "exit_code": 1,
            "duration": 0.1,
            "stdout_path": "stdout.log",
            "stderr_path": "stderr.log",
            "output_tail": "failed",
        },
        {
            "command": "pytest -q",
            "resolved_command": ["python", "-m", "pytest", "-q"],
            "cwd": "C:/repo",
            "python_executable": "C:/repo/.venv/Scripts/python.exe",
            "status": "FAILED",
            "exit_code": 0,
            "duration": 0.1,
            "stdout_path": "stdout.log",
            "stderr_path": "stderr.log",
            "output_tail": "passed",
        },
        {
            "command": "pytest -q",
            "resolved_command": ["python", "-m", "pytest", "-q"],
            "cwd": "C:/repo",
            "python_executable": "C:/repo/.venv/Scripts/python.exe",
            "status": "TIMEOUT",
            "exit_code": 124,
            "duration": 0.1,
            "stdout_path": "stdout.log",
            "stderr_path": "stderr.log",
            "output_tail": "timeout",
        },
    ],
)
def test_test_result_rejects_status_exit_code_mismatch(payload: dict) -> None:
    with pytest.raises(ValidationError):
        ExecutionResult.model_validate(payload)


def test_test_result_rejects_unknown_fields_and_negative_duration() -> None:
    with pytest.raises(ValidationError):
        ExecutionResult(
            command="pytest -q",
            resolved_command=["python", "-m", "pytest", "-q"],
            cwd="C:/repo",
            python_executable="C:/repo/.venv/Scripts/python.exe",
            status="PASSED",
            exit_code=0,
            duration=-1.0,
            stdout_path="stdout.log",
            stderr_path="stderr.log",
            output_tail="",
            raw_output="not allowed",
        )


def test_resolver_state_required_and_optional_keys() -> None:
    assert ResolverState.__required_keys__ == frozenset(
        {
            "run_id",
            "phase",
            "status",
            "cycle",
            "repo_path",
            "run_dir",
            "issue_input",
        }
    )
    assert ResolverState.__optional_keys__ == frozenset(
        {
            "base_commit",
            "project_type",
            "test_commands",
            "environment",
            "issue",
            "current_summary",
            "next_action",
            "explore_focus",
            "explore_focuses",
            "repair_round",
            "explore_stage_call",
            "explore_item_index",
            "coding_stage_call",
            "coding_iteration",
            "coding_task",
            "explore_reports",
            "explore_failures",
            "coding_result",
            "changed_files",
            "diff_path",
            "review_result",
            "test_results",
            "latest_test_results",
            "max_cycles",
            "agent_recursion_limit",
            "max_explore_batches",
            "test_timeout",
            "test_tail_lines",
            "rollback_required",
            "rollback_success",
            "rollback_failure",
            "failure",
        }
    )


def test_state_literal_values() -> None:
    assert get_args(Phase) == (
        "INITIALIZE",
        "PARSE_ISSUE",
        "COORDINATE",
        "EXPLORE",
        "CODE",
        "REVIEW",
        "TEST",
        "FINALIZE",
    )
    assert get_args(RunStatus) == ("RUNNING", "FINISHED", "FAILED")
    assert get_args(NextAction) == ("EXPLORE", "CODE", "FINISH", "FAILED")
