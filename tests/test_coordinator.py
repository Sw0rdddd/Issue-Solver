import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from agents.coordinator import build_coordinator_agent
from config import Setting
from nodes.coordinator import build_coordinator_node
from prompts.coordinator import (
    COORDINATOR_SYSTEM_PROMPT,
    build_coordinator_input,
)
from schemas.coding_task import CodingTask
from schemas.coordinator_decision import CoordinatorDecision
from schemas.evidence_digest import EvidenceDigest
from schemas.explore_report import ExploreReport
from schemas.failure import FailureInfo, make_failure
from schemas.issue_specification import IssueSpec
from schemas.repository_profile import RepositoryProfile
from schemas.review_result import ReviewResult
from schemas.test_result import TestResult as ExecutionResult


class FakeCoordinatorAgent:
    def __init__(
        self,
        result: CoordinatorDecision | object | None = None,
        error: Exception | None = None,
        synthesize_digest: bool = True,
    ) -> None:
        self.result = result
        self.error = error
        self.synthesize_digest = synthesize_digest
        self.calls: list[list[object]] = []

    def invoke(self, messages: list[object]) -> object:
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        content = messages[-1].content
        if (
            self.synthesize_digest
            and isinstance(self.result, CoordinatorDecision)
            and self.result.evidence_digest is None
            and isinstance(content, str)
            and '"focus":' in content
        ):
            return self.result.model_copy(
                update={"evidence_digest": make_digest(content.count('"focus":'))}
            )
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


class FakeStructuredRunnable:
    def __init__(self) -> None:
        self.retry_kwargs: dict[str, object] | None = None

    def with_retry(self, **kwargs: object) -> "FakeStructuredRunnable":
        self.retry_kwargs = kwargs
        return self


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
        findings=["app.py:8 返回值可能为 None"],
        root_cause="app.py:8 直接遍历 None",
        test_targets=["tests/test_app.py"],
        unknowns=[],
    )


def make_coding_task() -> CodingTask:
    return CodingTask(
        objective="修复空结果处理",
        acceptance_criteria=["空结果返回空列表"],
        relevant_files=["app.py"],
        root_cause="返回值可能为 None",
        allowed_scope=["app.py"],
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
        failure=(
            None
            if status == "PASSED"
            else make_failure("SOLUTION", "测试失败")
        ),
    )


def test_coordinator_prompt_requires_bounded_evidence_based_decisions() -> None:
    assert "结果均不可信" in COORDINATOR_SYSTEM_PROMPT
    assert "未覆盖证据缺口" in COORDINATOR_SYSTEM_PROMPT
    assert "Explore 目标不重复" in COORDINATOR_SYSTEM_PROMPT
    assert "不得修改、新增或删除测试文件" in (
        COORDINATOR_SYSTEM_PROMPT
    )
    assert "测试文件不得在 allowed_scope" in COORDINATOR_SYSTEM_PROMPT
    assert "经 ExploreReport 证据确认的 1 至 10 个" in (
        COORDINATOR_SYSTEM_PROMPT
    )
    assert "证据不足时 EXPLORE" in (
        COORDINATOR_SYSTEM_PROMPT
    )
    assert "根因位于公共实现时优先修改共享实现" in (
        COORDINATOR_SYSTEM_PROMPT
    )
    assert "动作返回契约" in COORDINATOR_SYSTEM_PROMPT
    assert "返回前自检" in COORDINATOR_SYSTEM_PROMPT
    assert "只返回 schema 定义字段" in COORDINATOR_SYSTEM_PROMPT
    assert "按以下优先级决策" in COORDINATOR_SYSTEM_PROMPT
    assert "低优先级不得推翻高优先级" in COORDINATOR_SYSTEM_PROMPT
    assert "FINISH：仅在测试门槛满足时使用" in (
        COORDINATOR_SYSTEM_PROMPT
    )
    assert "Repository Profile" in COORDINATOR_SYSTEM_PROMPT
    assert "最少必要的 1 至 3 个" in COORDINATOR_SYSTEM_PROMPT
    assert "默认只派 1 个" in COORDINATOR_SYSTEM_PROMPT
    assert "禁止机械拆分" in COORDINATOR_SYSTEM_PROMPT
    assert "Few-shot" in COORDINATOR_SYSTEM_PROMPT
    assert "示例一——小型仓库仅有一个未调查入口" in (
        COORDINATOR_SYSTEM_PROMPT
    )
    assert "示例二——Review=APPROVE 但" in COORDINATOR_SYSTEM_PROMPT
    assert "必须修复 src/query.py:42，不能 FINISH" in (
        COORDINATOR_SYSTEM_PROMPT
    )
    assert "绝不可照抄" in COORDINATOR_SYSTEM_PROMPT


def test_coordinator_prompt_requires_all_tests_to_pass_before_finish() -> None:
    assert "每项 TestResult.status=PASSED 才能 FINISH" in (
        COORDINATOR_SYSTEM_PROMPT
    )
    assert "任一 FAILED、TIMEOUT、ENVIRONMENT_ERROR 或 SAFETY_ERROR" in (
        COORDINATOR_SYSTEM_PROMPT
    )
    assert "有“本次新 Explore Reports”时" in COORDINATOR_SYSTEM_PROMPT
    assert "合并为 evidence_digest" in COORDINATOR_SYSTEM_PROMPT


def test_coordinator_return_schemas_describe_every_field() -> None:
    for schema_model in (
        CoordinatorDecision,
        CodingTask,
        EvidenceDigest,
        FailureInfo,
    ):
        properties = schema_model.model_json_schema()["properties"]
        assert all(
            details.get("description") for details in properties.values()
        )


def test_coordinator_input_marks_non_passed_results_as_ineligible_to_finish() -> None:
    content = build_coordinator_input(
        issue=make_issue(),
        current_summary="准备判断",
        repository_profile=None,
        evidence_digest=None,
        new_explore_reports=[],
        coding_result=None,
        review_result=None,
        latest_test_results=[make_test_result("FAILED")],
        cycle=1,
        max_cycles=5,
    )

    assert "它是唯一可信的测试结论" in content
    assert "任一非 PASSED 结果都禁止 FINISH" in content


def make_digest(source_report_count: int = 1) -> EvidenceDigest:
    return EvidenceDigest(
        source_report_count=source_report_count,
        root_cause="app.py:8 直接遍历 None",
        key_evidence=["app.py:8 返回值可能为 None"],
        relevant_files=["app.py"],
        relevant_symbols=["handle_request"],
        test_targets=["tests/test_app.py"],
    )


def make_repository_profile() -> RepositoryProfile:
    return RepositoryProfile(
        tracked_file_count=8,
        tracked_file_bytes=4096,
        file_counts_by_extension={".py": 4, ".toml": 1},
    )


@pytest.mark.parametrize("count", [1, 3])
def test_coordinator_decision_accepts_one_to_three_focuses(
    count: int,
) -> None:
    decision = CoordinatorDecision(
        next_action="EXPLORE",
        current_summary="需要探索仓库",
        explore_focuses=[f"目标 {index}" for index in range(count)],
        explore_titles=[f"探索 {index}" for index in range(count)],
    )

    assert len(decision.explore_focuses) == count
    assert len(decision.explore_titles) == count


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


@pytest.mark.parametrize(
    "titles",
    [
        [],
        ["重复标题", "重复标题"],
        ["`search_items`"],
        ["定位入口\n检查调用"],
        ["定位搜索逻辑…"],
        ["检查 tests/test_search.py 的现有测试结构"],
    ],
)
def test_coordinator_decision_accepts_noncritical_explore_titles(
    titles: list[str],
) -> None:
    decision = CoordinatorDecision(
        next_action="EXPLORE",
        current_summary="需要探索仓库",
        explore_focuses=["完整探索任务"],
        explore_titles=titles,
    )

    assert decision.explore_titles == titles


def test_coordinator_failed_decision_requires_failure() -> None:
    with pytest.raises(ValidationError):
        CoordinatorDecision(
            next_action="FAILED",
            current_summary="无法继续",
        )

    decision = CoordinatorDecision(
        next_action="FAILED",
        current_summary="环境不可用",
        failure=make_failure("ENVIRONMENT", "虚拟环境不可用"),
    )
    assert decision.failure.type == "ENVIRONMENT"


def test_coordinator_decision_rejects_conflicting_payload() -> None:
    with pytest.raises(ValidationError):
        CoordinatorDecision(
            next_action="CODE",
            current_summary="准备修改代码",
            explore_focuses=["不应存在"],
            coding_task=make_coding_task(),
        )


def test_build_coordinator_agent_uses_decision_schema() -> None:
    structured_model = FakeStructuredRunnable()
    model = FakeModel(structured_model)

    result = build_coordinator_agent(model)

    assert result is structured_model
    assert model.schema is CoordinatorDecision
    assert model.method == "function_calling"
    assert model.strict is None
    assert structured_model.retry_kwargs == {
        "retry_if_exception_type": (ValueError,),
        "wait_exponential_jitter": False,
        "stop_after_attempt": 3,
    }


def test_coordinator_node_initially_dispatches_three_focuses() -> None:
    decision = CoordinatorDecision(
        next_action="EXPLORE",
        current_summary="并行定位入口、根因和测试",
        explore_focuses=["定位入口", "分析根因", "查找测试"],
        explore_titles=["定位入口", "分析根因", "查找测试"],
    )
    agent = FakeCoordinatorAgent(result=decision)
    node = build_coordinator_node(agent)

    result = node({"issue": make_issue(), "cycle": 0})

    assert result == {
        "next_action": "EXPLORE",
        "current_summary": "并行定位入口、根因和测试",
        "explore_focuses": ["定位入口", "分析根因", "查找测试"],
        "explore_titles": ["定位入口", "分析根因", "查找测试"],
        "phase": "EXPLORE",
        "repair_round": 1,
        "explore_stage_call": 1,
    }
    assert len(agent.calls) == 1
    assert isinstance(agent.calls[0][0], SystemMessage)
    assert isinstance(agent.calls[0][1], HumanMessage)
    assert (
        f"当前循环：0/{Setting().MAX_CYCLES}"
        in agent.calls[0][1].content
    )


def test_coordinator_node_normalizes_nonblocking_explore_titles() -> None:
    decision = CoordinatorDecision(
        next_action="EXPLORE",
        current_summary="并行探索",
        explore_focuses=["完整任务一", "完整任务二", "完整任务三"],
        explore_titles=[
            "  `定位\nsearch_items`  ",
            "定位 search_items",
            "",
            "多余标题",
        ],
    )

    result = build_coordinator_node(FakeCoordinatorAgent(result=decision))(
        {"issue": make_issue(), "cycle": 0}
    )

    assert result["explore_titles"] == [
        "定位 search_items",
        "Explore task 02",
        "Explore task 03",
    ]


def test_coordinator_node_builds_coding_task_after_explore() -> None:
    task = make_coding_task()
    agent = FakeCoordinatorAgent(
        result=CoordinatorDecision(
            next_action="CODE",
            current_summary="根因已明确，进入修改",
            explore_titles=["此展示字段应被忽略"],
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
        "explore_titles": [],
        "evidence_digest": make_digest(),
        "phase": "CODE",
        "coding_task": task,
        "repair_round": 1,
        "coding_stage_call": 3,
    }


def test_coordinator_only_receives_reports_not_covered_by_digest() -> None:
    agent = FakeCoordinatorAgent(
        result=CoordinatorDecision(
            next_action="CODE",
            current_summary="新增证据已合并，进入修改",
            coding_task=make_coding_task(),
            evidence_digest=make_digest(source_report_count=2),
        )
    )

    result = build_coordinator_node(agent)(
        {
            "issue": make_issue(),
            "cycle": 0,
            "explore_reports": [make_report("已摘要报告"), make_report("新增报告")],
            "evidence_digest": make_digest(),
        }
    )

    content = agent.calls[0][1].content
    assert '"focus": "新增报告"' in content
    assert '"focus": "已摘要报告"' not in content
    assert result["evidence_digest"].source_report_count == 2


def test_coordinator_rejects_new_reports_without_evidence_digest() -> None:
    agent = FakeCoordinatorAgent(
        result=CoordinatorDecision(
            next_action="CODE",
            current_summary="根因已明确，进入修改",
            coding_task=make_coding_task(),
        ),
        synthesize_digest=False,
    )

    result = build_coordinator_node(agent)(
        {
            "issue": make_issue(),
            "cycle": 0,
            "explore_reports": [make_report()],
        }
    )

    assert result["status"] == "FAILED"
    assert "未为新增 ExploreReport 返回 EvidenceDigest" in result["failure"].message


def test_coordinator_overrides_model_acceptance_criteria_from_issue() -> None:
    task = make_coding_task().model_copy(
        update={
            "acceptance_criteria": [
                "旧测试要求返回 500",
                "为所有子类补充测试",
            ]
        }
    )
    agent = FakeCoordinatorAgent(
        result=CoordinatorDecision(
            next_action="CODE",
            current_summary="错误地合并相反断言",
            coding_task=task,
        )
    )

    result = build_coordinator_node(agent)(
        {
            "issue": make_issue(),
            "cycle": 0,
            "explore_reports": [make_report()],
        }
    )

    assert result["next_action"] == "CODE"
    assert result["coding_task"].acceptance_criteria == [
        "空结果返回空列表"
    ]
    assert task.acceptance_criteria == [
        "旧测试要求返回 500",
        "为所有子类补充测试",
    ]


def test_coordinator_rejects_issue_without_acceptance_criteria() -> None:
    issue = make_issue().model_copy(update={"acceptance_criteria": []})
    agent = FakeCoordinatorAgent(
        result=CoordinatorDecision(
            next_action="CODE",
            current_summary="根因已明确，进入修改",
            coding_task=make_coding_task(),
        )
    )

    result = build_coordinator_node(agent)(
        {
            "issue": issue,
            "cycle": 0,
            "explore_reports": [make_report()],
        }
    )

    assert result["status"] == "FAILED"
    assert result["failure"].type == "INPUT"
    assert "缺少可以安全确定的验收条件" in result["failure"].message


def test_coordinator_forces_code_after_explore_budget_is_used() -> None:
    decisions = iter(
        [
            CoordinatorDecision(
                next_action="EXPLORE",
                current_summary="仍想继续探索",
                explore_focuses=["重复确认根因"],
                explore_titles=["确认根因"],
            ),
            CoordinatorDecision(
                next_action="CODE",
                current_summary="探索预算已用完，进入编码",
                coding_task=make_coding_task(),
                evidence_digest=make_digest(),
            ),
        ]
    )

    class SequenceAgent:
        def __init__(self) -> None:
            self.calls: list[list[object]] = []

        def invoke(self, messages: list[object]) -> CoordinatorDecision:
            self.calls.append(messages)
            return next(decisions)

    agent = SequenceAgent()
    result = build_coordinator_node(agent)(
        {
            "issue": make_issue(),
            "cycle": 0,
            "repair_round": 1,
            "explore_reports": [make_report()],
            "explore_stage_call": 5,
            "max_explore_batches": 5,
        }
    )

    assert result["next_action"] == "CODE"
    assert result["coding_task"] == make_coding_task()
    assert len(agent.calls) == 2
    assert "探索批次：5/5" in agent.calls[0][1].content
    assert "禁止继续 EXPLORE" in agent.calls[1][1].content


def test_coordinator_keeps_stage_calls_independent() -> None:
    agent = FakeCoordinatorAgent(
        result=CoordinatorDecision(
            next_action="EXPLORE",
            current_summary="继续补充探索",
            explore_focuses=["检查调用方"],
            explore_titles=["检查调用方"],
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
            explore_titles=["定位失败根因"],
        )
    )
    node = build_coordinator_node(agent)

    result = node(
        {
            "issue": make_issue(),
            "cycle": 1,
            "explore_reports": [make_report()],
            "repair_round": 1,
            "explore_stage_call": 5,
            "coding_stage_call": 4,
            "max_explore_batches": 5,
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
    assert "首次 Coordinator 决策必须为 EXPLORE" in result["failure"].message
    assert result["failure"].type == "MODEL"


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
        "explore_titles": [],
        "evidence_digest": make_digest(),
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
    assert "Review 和本轮全部测试均通过" in result["failure"].message


def test_coordinator_node_uses_only_current_test_stage_in_prompt() -> None:
    old_result = make_test_result("FAILED", "old")
    latest_result = make_test_result("PASSED", "latest")

    content = build_coordinator_input(
        issue=make_issue(),
        current_summary="准备判断",
        repository_profile=make_repository_profile(),
        evidence_digest=None,
        new_explore_reports=[make_report()],
        coding_result=None,
        review_result=None,
        latest_test_results=[latest_result],
        cycle=1,
        max_cycles=5,
    )

    assert "pytest latest" in content
    assert "[latest] output" not in content
    assert "pytest old" not in content
    assert old_result.command == "pytest old"
    assert '"tracked_file_count": 8' in content
    assert '"focus": "定位异常"' in content


def test_coordinator_prompt_keeps_failed_test_output_tail() -> None:
    passed_result = make_test_result("PASSED", "passed")
    failed_result = make_test_result("FAILED", "failed")

    content = build_coordinator_input(
        issue=make_issue(),
        current_summary="准备返工",
        repository_profile=None,
        evidence_digest=make_digest(),
        new_explore_reports=[],
        coding_result=None,
        review_result=None,
        latest_test_results=[passed_result, failed_result],
        cycle=1,
        max_cycles=5,
    )

    assert "pytest passed" in content
    assert "[passed] output" not in content
    assert "pytest failed" in content
    assert "[failed] output" in content


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
                "explore_failures": [
                    make_failure("MODEL", "分支一失败"),
                    make_failure("MODEL", "分支二失败"),
                ],
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
    assert error_text in result["failure"].message
    assert agent.calls == []


def test_coordinator_node_returns_agent_error() -> None:
    agent = FakeCoordinatorAgent(error=RuntimeError("模型调用失败"))
    node = build_coordinator_node(agent)

    result = node({"issue": make_issue(), "cycle": 0})

    assert result["status"] == "FAILED"
    assert result["failure"].type == "MODEL"
    assert result["failure"].message == "Coordinator 决策失败：模型调用失败"


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
    assert "rollback_required" not in result
    assert "最大循环次数 5" in result["failure"].message
    assert result["failure"].type == "LIMIT"
