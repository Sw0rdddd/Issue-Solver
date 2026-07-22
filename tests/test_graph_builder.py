from graph import builder
from schemas.coding_result import CodingResult
from schemas.coding_task import CodingTask
from schemas.explore_report import ExploreReport
from schemas.failure import make_failure
from schemas.issue_specification import IssueSpec
from schemas.review_result import ReviewResult
from schemas.test_result import TestResult as ExecutionResult


def test_build_graph_registers_existing_nodes(monkeypatch) -> None:
    model = object()
    non_thinking_model = object()
    parse_issue_node = lambda state: {"issue": state}
    coordinator_agent = object()
    coordinator_node = lambda state: {"next_action": "EXPLORE"}
    explore_agent = object()
    explore_node = lambda state: {"explore_reports": [state]}
    coding_node = lambda state: {"phase": "REVIEW"}
    review_agent = object()
    review_node = lambda state: {"phase": "TEST"}
    test_node = lambda state: {"phase": "COORDINATE"}
    finalize_node = lambda state: {"phase": "FINALIZE"}
    calls: list[tuple[str, object]] = []
    factories: dict[str, object] = {}

    def fake_build_parse_issue_node(value: object):
        calls.append(("parse_issue", value))
        return parse_issue_node

    def fake_build_non_thinking_model(value: object) -> object:
        calls.append(("non_thinking_model", value))
        return non_thinking_model

    def fake_build_explore_agent(value: object, repo_path: str):
        calls.append(("explore_agent", (value, repo_path)))
        return explore_agent

    def fake_build_coordinator_agent(value: object):
        calls.append(("coordinator_agent", value))
        return coordinator_agent

    def fake_build_coordinator_node(value: object):
        calls.append(("coordinator_node", value))
        return coordinator_node

    def fake_build_explore_node(value: object):
        factories["explore"] = value
        calls.append(("explore_node", None))
        return explore_node

    def fake_build_coding_node(value: object):
        calls.append(("coding_node", value))
        return coding_node

    def fake_build_review_agent(
        value: object,
        repo_path: str,
        base_commit: str,
    ):
        calls.append(("review_agent", (value, repo_path, base_commit)))
        return review_agent

    def fake_build_review_node(value: object):
        factories["review"] = value
        calls.append(("review_node", None))
        return review_node

    monkeypatch.setattr(
        builder,
        "build_parse_issue_node",
        fake_build_parse_issue_node,
    )
    monkeypatch.setattr(
        builder,
        "build_non_thinking_model",
        fake_build_non_thinking_model,
    )
    monkeypatch.setattr(
        builder,
        "build_coordinator_agent",
        fake_build_coordinator_agent,
    )
    monkeypatch.setattr(
        builder,
        "build_coordinator_node",
        fake_build_coordinator_node,
    )
    monkeypatch.setattr(
        builder,
        "build_explore_agent",
        fake_build_explore_agent,
    )
    monkeypatch.setattr(
        builder,
        "build_explore_node",
        fake_build_explore_node,
    )
    monkeypatch.setattr(
        builder,
        "build_coding_node",
        fake_build_coding_node,
    )
    monkeypatch.setattr(builder, "build_review_agent", fake_build_review_agent)
    monkeypatch.setattr(builder, "build_review_node", fake_build_review_node)
    monkeypatch.setattr(builder, "build_test_node", lambda: test_node)
    monkeypatch.setattr(builder, "build_finalize_node", lambda: finalize_node)

    graph = builder.build_graph(model)

    assert set(graph.nodes) == {
        "initialize",
        "parse_issue",
        "coordinator",
        "explore",
        "coding",
        "review",
        "test",
        "finalize",
    }
    assert calls == [
        ("non_thinking_model", model),
        ("parse_issue", non_thinking_model),
        ("coordinator_agent", model),
        ("coordinator_node", coordinator_agent),
        ("explore_node", None),
        ("coding_node", model),
        ("review_node", None),
    ]
    assert factories["explore"]("C:/repo") is explore_agent
    assert factories["review"]("C:/repo", "abc123") is review_agent
    assert calls[-2:] == [
        ("explore_agent", (non_thinking_model, "C:/repo")),
        ("review_agent", (model, "C:/repo", "abc123")),
    ]
    assert ("__start__", "initialize") in graph.edges
    assert ("explore", "coordinator") in graph.edges
    assert ("finalize", "__end__") in graph.edges
    assert set(graph.branches) == {
        "initialize",
        "parse_issue",
        "coordinator",
        "coding",
        "review",
        "test",
    }


def test_compiled_graph_fans_out_and_joins_explore_nodes(
    monkeypatch,
) -> None:
    issue = IssueSpec(title="查询失败", body="空结果返回 500")
    coding_task = CodingTask(
        objective="修复空结果处理",
        acceptance_criteria=["返回空列表"],
        relevant_files=["app.py"],
        root_cause="返回值可能为 None",
        allowed_scope=["app.py"],
        test_targets=["tests/test_app.py"],
    )
    explore_calls: list[tuple[str, int, int, int]] = []
    coordinator_calls: list[int] = []
    coding_calls: list[CodingTask] = []

    def fake_initialize_node(state: dict) -> dict:
        return {"phase": "PARSE_ISSUE"}

    def fake_parse_issue_node(state: dict) -> dict:
        return {"issue": issue, "phase": "COORDINATE"}

    def fake_coordinator_node(state: dict) -> dict:
        coordinator_calls.append(len(state.get("explore_reports", [])))
        if not state.get("explore_reports"):
            return {
                "next_action": "EXPLORE",
                "current_summary": "并行探索",
                "explore_focuses": ["入口", "根因", "测试"],
                "phase": "EXPLORE",
                "repair_round": 1,
                "explore_stage_call": 1,
            }
        return {
            "next_action": "CODE",
            "current_summary": "信息充分",
            "explore_focuses": [],
            "coding_task": coding_task,
            "phase": "CODE",
            "repair_round": 1,
            "coding_stage_call": 1,
        }

    def fake_explore_node(state: dict) -> dict:
        focus = state["explore_focus"]
        explore_calls.append(
            (
                focus,
                state["repair_round"],
                state["explore_stage_call"],
                state["explore_item_index"],
            )
        )
        return {
            "explore_reports": [
                ExploreReport(
                    focus=focus,
                    relevant_files=[],
                    relevant_symbols=[],
                    findings=[],
                    root_cause="",
                    test_targets=[],
                    unknowns=[],
                )
            ]
        }

    def fake_coding_node(state: dict) -> dict:
        coding_calls.append(state["coding_task"])
        return {
            "phase": "REVIEW",
            "coding_result": CodingResult(
                success=True,
                changed_files=["app.py"],
                summary="完成修改",
                diff_path=None,
                validation=["检查 Diff"],
                remaining_risks=[],
            ),
            "changed_files": ["app.py"],
            "coding_iteration": 1,
        }

    monkeypatch.setattr(builder, "initialize_node", fake_initialize_node)
    monkeypatch.setattr(
        builder,
        "build_parse_issue_node",
        lambda model: fake_parse_issue_node,
    )
    monkeypatch.setattr(
        builder,
        "build_coordinator_agent",
        lambda model: object(),
    )
    monkeypatch.setattr(
        builder,
        "build_coordinator_node",
        lambda agent: fake_coordinator_node,
    )
    monkeypatch.setattr(
        builder,
        "build_explore_agent",
        lambda model: object(),
    )
    monkeypatch.setattr(
        builder,
        "build_explore_node",
        lambda agent: fake_explore_node,
    )
    monkeypatch.setattr(
        builder,
        "build_coding_node",
        lambda model: fake_coding_node,
    )
    monkeypatch.setattr(builder, "build_review_agent", lambda model: object())
    monkeypatch.setattr(
        builder,
        "build_review_node",
        lambda agent: lambda state: {
            "phase": "REVIEW",
            "status": "FAILED",
            "failure": make_failure("MODEL", "测试在 Review 停止"),
        },
    )
    monkeypatch.setattr(builder, "build_test_node", lambda: lambda state: {})
    monkeypatch.setattr(builder, "build_finalize_node", lambda: lambda state: {})

    graph = builder.build_graph(object()).compile()
    result = graph.invoke(
        {
            "run_id": "run_test",
            "phase": "INITIALIZE",
            "status": "RUNNING",
            "cycle": 0,
            "repo_path": ".",
            "run_dir": ".",
            "issue_input": "查询失败",
            "explore_reports": [],
            "explore_failures": [],
            "explore_stage_call": 0,
            "coding_stage_call": 0,
        }
    )

    assert coordinator_calls == [0, 3]
    assert sorted(explore_calls) == [
        ("入口", 1, 1, 1),
        ("根因", 1, 1, 2),
        ("测试", 1, 1, 3),
    ]
    assert len(result["explore_reports"]) == 3
    assert result["next_action"] == "CODE"
    assert result["coding_task"] == coding_task
    assert result["phase"] == "REVIEW"
    assert result["coding_iteration"] == 1
    assert coding_calls == [coding_task]


def test_compiled_graph_runs_review_test_and_finalize_without_coordinator(
    monkeypatch,
) -> None:
    issue = IssueSpec(title="查询失败", body="应返回空列表")
    task = CodingTask(
        objective="修复查询",
        acceptance_criteria=["返回空列表"],
        relevant_files=["app.py"],
        root_cause="缺少空值处理",
        allowed_scope=["app.py"],
        test_targets=["tests/test_app.py"],
    )
    review_result = ReviewResult(
        verdict="APPROVE",
        issues=[],
        suggestions=[],
        remaining_risks=[],
    )
    test_result = ExecutionResult(
        command="pytest -q",
        resolved_command=["C:/repo/.venv/Scripts/python.exe", "-m", "pytest", "-q"],
        cwd="C:/repo",
        python_executable="C:/repo/.venv/Scripts/python.exe",
        status="PASSED",
        exit_code=0,
        duration=0.1,
        stdout_path="stdout.log",
        stderr_path="stderr.log",
        output_tail="[stdout] passed",
    )
    calls: list[str] = []

    monkeypatch.setattr(
        builder,
        "initialize_node",
        lambda state: {"phase": "PARSE_ISSUE"},
    )
    monkeypatch.setattr(
        builder,
        "build_parse_issue_node",
        lambda model: lambda state: {"issue": issue, "phase": "COORDINATE"},
    )
    monkeypatch.setattr(builder, "build_coordinator_agent", lambda model: object())

    def coordinator_node(state: dict) -> dict:
        if not state.get("explore_reports"):
            return {
                "next_action": "EXPLORE",
                "explore_focuses": ["定位"],
                "repair_round": 1,
                "explore_stage_call": 1,
            }
        if not state.get("coding_result"):
            return {
                "next_action": "CODE",
                "coding_task": task,
                "repair_round": 1,
                "coding_stage_call": 1,
            }
        calls.append("unexpected_coordinator_after_test")
        raise AssertionError("测试通过后不应再次调用 Coordinator")

    monkeypatch.setattr(
        builder,
        "build_coordinator_node",
        lambda agent: coordinator_node,
    )
    monkeypatch.setattr(builder, "build_explore_agent", lambda model: object())
    monkeypatch.setattr(
        builder,
        "build_explore_node",
        lambda agent: lambda state: {
            "explore_reports": [
                ExploreReport(
                    focus="定位",
                    relevant_files=["app.py"],
                    relevant_symbols=[],
                    findings=[],
                    root_cause="缺少空值处理",
                    test_targets=[],
                    unknowns=[],
                )
            ]
        },
    )
    monkeypatch.setattr(
        builder,
        "build_coding_node",
        lambda model: lambda state: {
            "phase": "REVIEW",
            "coding_result": CodingResult(
                success=True,
                changed_files=["app.py"],
                summary="完成修改",
                diff_path=None,
                validation=["检查 Diff"],
                remaining_risks=[],
            ),
            "changed_files": ["app.py"],
        },
    )
    monkeypatch.setattr(builder, "build_review_agent", lambda model: object())
    monkeypatch.setattr(
        builder,
        "build_review_node",
        lambda agent: lambda state: {
            "phase": "TEST",
            "review_result": review_result,
        },
    )
    monkeypatch.setattr(
        builder,
        "build_test_node",
        lambda: lambda state: {
            "phase": "FINALIZE",
            "next_action": "FINISH",
            "cycle": 1,
            "test_results": [test_result],
            "latest_test_results": [test_result],
        },
    )
    monkeypatch.setattr(
        builder,
        "build_finalize_node",
        lambda: lambda state: {
            "phase": "FINALIZE",
            "status": "FINISHED",
            "diff_path": "diff.patch",
        },
    )

    graph = builder.build_graph(object()).compile()
    result = graph.invoke(
        {
            "run_id": "run_test",
            "phase": "INITIALIZE",
            "status": "RUNNING",
            "cycle": 0,
            "repo_path": ".",
            "run_dir": ".",
            "issue_input": "查询失败",
            "explore_reports": [],
            "explore_failures": [],
            "test_results": [],
        }
    )

    assert calls == []
    assert result["status"] == "FINISHED"
    assert result["phase"] == "FINALIZE"
    assert result["diff_path"] == "diff.patch"
