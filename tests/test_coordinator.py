import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from agents.coordinator import build_coordinator_agent
from nodes.coordinator import build_coordinator_node
from prompts.coordinator import (
    COORDINATOR_SYSTEM_PROMPT,
    build_coordinator_input,
)
from schemas.coding_task import CodingTask
from schemas.coordinator_decision import CoordinatorDecision
from schemas.explore_report import ExploreReport
from schemas.issue_specification import IssueSpec
from schemas.review_result import ReviewResult
from schemas.test_result import TestResult as ExecutionResult


class FakeCoordinatorAgent:
    def __init__(
        self,
        result: CoordinatorDecision | object | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[list[object]] = []

    def invoke(self, messages: list[object]) -> object:
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return self.result


class FakeModel:
    def __init__(self, structured_model: object) -> None:
        self.structured_model = structured_model
        self.schema: type | None = None
        self.method: str | None = None
        self.strict: bool | None = False

    def with_structured_output(
        self,
        schema: type,
        *,
        method: str,
        strict: bool | None,
    ) -> object:
        self.schema = schema
        self.method = method
        self.strict = strict
        return self.structured_model


def make_issue() -> IssueSpec:
    return IssueSpec(
        title="空结果查询失败",
        body="查询没有结果时返回 500",
        expected_behavior="返回空列表",
        actual_behavior="返回 500",
        acceptance_criteria=["空结果返回空列表"],
    )


def make_report(focus: str = "定位异常") -> ExploreReport:
    return ExploreReport(
        focus=focus,
        relevant_files=["app.py"],
        relevant_symbols=["handle_request"],
        findings=["返回值可能为 None"],
        root_cause="直接遍历 None",
        test_targets=["tests/test_app.py"],
        unknowns=[],
    )


def make_coding_task() -> CodingTask:
    return CodingTask(
        objective="修复空结果处理",
        acceptance_criteria=["空结果返回空列表"],
        relevant_files=["app.py"],
        root_cause="返回值可能为 None",
        allowed_scope=["app.py", "tests/test_app.py"],
        test_targets=["tests/test_app.py"],
    )


def make_test_result(status: str, name: str = "latest") -> ExecutionResult:
    return ExecutionResult(
        command=f"pytest {name}",
        resolved_command=["C:/repo/.venv/Scripts/python.exe", "-m", "pytest", name],
        cwd="C:/repo",
        python_executable="C:/repo/.venv/Scripts/python.exe",
        status=status,
        exit_code=0 if status == "PASSED" else 1,
        duration=0.1,
        stdout_path=f"{name}.out",
        stderr_path=f"{name}.err",
        output_tail=f"[{name}] output",
    )


def test_coordinator_prompt_requires_nested_coding_task_object() -> None:
    assert "coding_task 必须是 JSON 对象" in COORDINATOR_SYSTEM_PROMPT
    assert "禁止将 coding_task 序列化为 JSON 字符串" in COORDINATOR_SYSTEM_PROMPT
    assert '"explore_focuses": []' in COORDINATOR_SYSTEM_PROMPT
    assert '"coding_task": {' in COORDINATOR_SYSTEM_PROMPT
    assert '"acceptance_criteria": [' in COORDINATOR_SYSTEM_PROMPT
    assert '"relevant_files": [' in COORDINATOR_SYSTEM_PROMPT
    assert '"allowed_scope": [' in COORDINATOR_SYSTEM_PROMPT
    assert '"test_targets": [' in COORDINATOR_SYSTEM_PROMPT

    for field in (
        "objective",
        "acceptance_criteria",
        "relevant_files",
        "root_cause",
        "allowed_scope",
        "test_targets",
    ):
        assert f'"{field}":' in COORDINATOR_SYSTEM_PROMPT


@pytest.mark.parametrize("count", [1, 3])
def test_coordinator_decision_accepts_one_to_three_focuses(
    count: int,
) -> None:
    decision = CoordinatorDecision(
        next_action="EXPLORE",
        current_summary="需要探索仓库",
        explore_focuses=[f"目标 {index}" for index in range(count)],
    )

    assert len(decision.explore_focuses) == count


@pytest.mark.parametrize("focuses", [[], ["1", "2", "3", "4"]])
def test_coordinator_decision_rejects_invalid_focus_count(
    focuses: list[str],
) -> None:
    with pytest.raises(ValidationError):
        CoordinatorDecision(
            next_action="EXPLORE",
            current_summary="需要探索仓库",
            explore_focuses=focuses,
        )


def test_coordinator_decision_requires_task_for_code() -> None:
    with pytest.raises(ValidationError):
        CoordinatorDecision(
            next_action="CODE",
            current_summary="准备修改代码",
        )


def test_coordinator_decision_rejects_conflicting_payload() -> None:
    with pytest.raises(ValidationError):
        CoordinatorDecision(
            next_action="CODE",
            current_summary="准备修改代码",
            explore_focuses=["不应存在"],
            coding_task=make_coding_task(),
        )


def test_build_coordinator_agent_uses_decision_schema() -> None:
    structured_model = object()
    model = FakeModel(structured_model)

    result = build_coordinator_agent(model)

    assert result is structured_model
    assert model.schema is CoordinatorDecision
    assert model.method == "function_calling"
    assert model.strict is None


def test_coordinator_node_initially_dispatches_three_focuses() -> None:
    decision = CoordinatorDecision(
        next_action="EXPLORE",
        current_summary="并行定位入口、根因和测试",
        explore_focuses=["定位入口", "分析根因", "查找测试"],
    )
    agent = FakeCoordinatorAgent(result=decision)
    node = build_coordinator_node(agent)

    result = node({"issue": make_issue(), "cycle": 0})

    assert result == {
        "next_action": "EXPLORE",
        "current_summary": "并行定位入口、根因和测试",
        "explore_focuses": ["定位入口", "分析根因", "查找测试"],
        "phase": "EXPLORE",
        "repair_round": 1,
        "explore_stage_call": 1,
    }
    assert len(agent.calls) == 1
    assert isinstance(agent.calls[0][0], SystemMessage)
    assert isinstance(agent.calls[0][1], HumanMessage)
    assert "当前循环：0/5" in agent.calls[0][1].content


def test_coordinator_node_builds_coding_task_after_explore() -> None:
    task = make_coding_task()
    agent = FakeCoordinatorAgent(
        result=CoordinatorDecision(
            next_action="CODE",
            current_summary="根因已明确，进入修改",
            coding_task=task,
        )
    )
    node = build_coordinator_node(agent)

    result = node(
        {
            "issue": make_issue(),
            "cycle": 0,
            "explore_reports": [make_report()],
            "repair_round": 1,
            "coding_stage_call": 2,
        }
    )

    assert result == {
        "next_action": "CODE",
        "current_summary": "根因已明确，进入修改",
        "explore_focuses": [],
        "phase": "CODE",
        "coding_task": task,
        "repair_round": 1,
        "coding_stage_call": 3,
    }


def test_coordinator_keeps_stage_calls_independent() -> None:
    agent = FakeCoordinatorAgent(
        result=CoordinatorDecision(
            next_action="EXPLORE",
            current_summary="继续补充探索",
            explore_focuses=["检查调用方"],
        )
    )
    node = build_coordinator_node(agent)

    result = node(
        {
            "issue": make_issue(),
            "cycle": 1,
            "explore_reports": [make_report()],
            "repair_round": 2,
            "explore_stage_call": 2,
            "coding_stage_call": 4,
        }
    )

    assert result["repair_round"] == 2
    assert result["explore_stage_call"] == 3
    assert "coding_stage_call" not in result


def test_coordinator_resets_other_stage_counter_in_new_round() -> None:
    agent = FakeCoordinatorAgent(
        result=CoordinatorDecision(
            next_action="EXPLORE",
            current_summary="测试失败后重新探索",
            explore_focuses=["定位失败根因"],
        )
    )
    node = build_coordinator_node(agent)

    result = node(
        {
            "issue": make_issue(),
            "cycle": 1,
            "explore_reports": [make_report()],
            "repair_round": 1,
            "explore_stage_call": 3,
            "coding_stage_call": 4,
        }
    )

    assert result["repair_round"] == 2
    assert result["explore_stage_call"] == 1
    assert result["coding_stage_call"] == 0


def test_coordinator_node_requires_initial_explore() -> None:
    agent = FakeCoordinatorAgent(
        result=CoordinatorDecision(
            next_action="CODE",
            current_summary="错误地直接编码",
            coding_task=make_coding_task(),
        )
    )
    node = build_coordinator_node(agent)

    result = node({"issue": make_issue(), "cycle": 0})

    assert result["status"] == "FAILED"
    assert result["next_action"] == "FAILED"
    assert "首次 Coordinator 决策必须为 EXPLORE" in result["error"]


def test_coordinator_node_allows_finish_after_review_and_test_pass() -> None:
    agent = FakeCoordinatorAgent(
        result=CoordinatorDecision(
            next_action="FINISH",
            current_summary="审查和测试均通过",
        )
    )
    node = build_coordinator_node(agent)

    result = node(
        {
            "issue": make_issue(),
            "cycle": 1,
            "explore_reports": [make_report()],
            "review_result": ReviewResult(
                verdict="APPROVE",
                issues=[],
                suggestions=[],
                remaining_risks=[],
            ),
            "latest_test_results": [make_test_result("PASSED")],
        }
    )

    assert result == {
        "next_action": "FINISH",
        "current_summary": "审查和测试均通过",
        "explore_focuses": [],
        "phase": "FINALIZE",
    }


def test_coordinator_node_rejects_finish_without_passed_test() -> None:
    agent = FakeCoordinatorAgent(
        result=CoordinatorDecision(
            next_action="FINISH",
            current_summary="错误地结束",
        )
    )
    node = build_coordinator_node(agent)

    result = node(
        {
            "issue": make_issue(),
            "cycle": 1,
            "explore_reports": [make_report()],
            "review_result": ReviewResult(
                verdict="APPROVE",
                issues=[],
                suggestions=[],
                remaining_risks=[],
            ),
            "latest_test_results": [make_test_result("FAILED")],
        }
    )

    assert result["status"] == "FAILED"
    assert "Review 和本轮全部测试均通过" in result["error"]


def test_coordinator_node_uses_only_current_test_stage_in_prompt() -> None:
    old_result = make_test_result("FAILED", "old")
    latest_result = make_test_result("PASSED", "latest")

    content = build_coordinator_input(
        issue=make_issue(),
        current_summary="准备判断",
        explore_reports=[make_report()],
        coding_result=None,
        review_result=None,
        latest_test_results=[latest_result],
        cycle=1,
        max_cycles=5,
    )

    assert "pytest latest" in content
    assert "pytest old" not in content
    assert old_result.command == "pytest old"


@pytest.mark.parametrize(
    ("state", "error_text"),
    [
        (
            {"issue": make_issue(), "cycle": 5, "max_cycles": 5},
            "最大循环次数 5",
        ),
        (
            {
                "issue": make_issue(),
                "cycle": 0,
                "explore_errors": ["分支一失败", "分支二失败"],
            },
            "分支一失败；分支二失败",
        ),
    ],
)
def test_coordinator_node_fails_without_calling_agent(
    state: dict,
    error_text: str,
) -> None:
    agent = FakeCoordinatorAgent()
    node = build_coordinator_node(agent)

    result = node(state)

    assert result["status"] == "FAILED"
    assert result["next_action"] == "FAILED"
    assert error_text in result["error"]
    assert agent.calls == []


def test_coordinator_node_returns_agent_error() -> None:
    agent = FakeCoordinatorAgent(error=RuntimeError("模型调用失败"))
    node = build_coordinator_node(agent)

    result = node({"issue": make_issue(), "cycle": 0})

    assert result["status"] == "FAILED"
    assert result["error"] == "Coordinator 决策失败：模型调用失败"


def test_coordinator_allows_finish_on_last_configured_cycle() -> None:
    agent = FakeCoordinatorAgent(
        result=CoordinatorDecision(
            next_action="FINISH",
            current_summary="第五轮审查和测试均通过",
        )
    )
    node = build_coordinator_node(agent)
    result = node(
        {
            "issue": make_issue(),
            "cycle": 5,
            "max_cycles": 5,
            "explore_reports": [make_report()],
            "review_result": ReviewResult(
                verdict="APPROVE",
                issues=[],
                suggestions=[],
                remaining_risks=[],
            ),
            "latest_test_results": [make_test_result("PASSED")],
        }
    )

    assert result["next_action"] == "FINISH"
    assert result["phase"] == "FINALIZE"


def test_coordinator_requests_rollback_when_last_cycle_needs_rework() -> None:
    agent = FakeCoordinatorAgent(
        result=CoordinatorDecision(
            next_action="CODE",
            current_summary="第五轮仍需修改",
            coding_task=make_coding_task(),
        )
    )
    node = build_coordinator_node(agent)
    result = node(
        {
            "issue": make_issue(),
            "cycle": 5,
            "max_cycles": 5,
            "explore_reports": [make_report()],
            "changed_files": ["app.py"],
            "review_result": ReviewResult(
                verdict="REQUEST_CHANGES",
                issues=["仍有错误"],
                suggestions=[],
                remaining_risks=[],
            ),
            "latest_test_results": [make_test_result("FAILED")],
        }
    )

    assert result["status"] == "FAILED"
    assert result["rollback_required"] is True
    assert "最大循环次数 5" in result["error"]
