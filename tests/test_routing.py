import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from graph.routing import (
    route_after_coordinator,
    route_after_step,
    route_after_test,
)
from graph.state import ResolverState
from schemas.failure import make_failure
from schemas.explore_report import ExploreReport


def make_report() -> ExploreReport:
    return ExploreReport(
        focus="已有探索",
        relevant_files=[],
        relevant_symbols=[],
        findings=[],
        root_cause="",
        test_targets=[],
        unknowns=[],
    )


def test_route_after_step_fails_closed() -> None:
    assert route_after_step({"status": "RUNNING"}) == "CONTINUE"
    assert route_after_step({"status": "FAILED"}) == "FAILED"


def test_route_after_test_finishes_only_after_test_node_marks_success() -> None:
    assert route_after_test({"next_action": "FINISH"}) == "FINALIZE"
    assert route_after_test({"next_action": "CODE"}) == "COORDINATOR"
    assert route_after_test({"status": "FAILED", "next_action": "FINISH"}) == "FAILED"


def test_route_after_coordinator_dispatches_send_branches() -> None:
    routes = route_after_coordinator(
        {
            "status": "RUNNING",
            "next_action": "EXPLORE",
            "explore_focuses": ["入口", "根因", "测试"],
            "explore_titles": ["定位入口", "分析根因", "检查测试"],
            "explore_reports": [make_report()],
            "cycle": 1,
            "explore_stage_call": 2,
        }
    )

    assert isinstance(routes, list)
    assert all(isinstance(route, Send) for route in routes)
    assert [route.node for route in routes] == ["explore"] * 3
    assert [route.arg["explore_focus"] for route in routes] == [
        "入口",
        "根因",
        "测试",
    ]
    assert [route.arg["explore_title"] for route in routes] == [
        "定位入口",
        "分析根因",
        "检查测试",
    ]
    assert [route.arg["repair_round"] for route in routes] == [2, 2, 2]
    assert [route.arg["explore_stage_call"] for route in routes] == [2, 2, 2]
    assert [route.arg["explore_item_index"] for route in routes] == [1, 2, 3]


@pytest.mark.parametrize("action", ["CODE", "FINISH", "FAILED"])
def test_route_after_coordinator_returns_terminal_action(
    action: str,
) -> None:
    assert route_after_coordinator({"next_action": action}) == action


def test_route_after_coordinator_rejects_invalid_state() -> None:
    assert route_after_coordinator({}) == "FAILED"
    assert route_after_coordinator(
        {
            "next_action": "EXPLORE",
            "explore_focuses": [],
        }
    ) == "FAILED"
    assert route_after_coordinator(
        {
            "next_action": "EXPLORE",
            "explore_focuses": ["入口"],
            "explore_titles": [],
        }
    ) == "FAILED"


def test_parallel_explore_errors_are_reduced_before_failure() -> None:
    coordinator_calls: list[list[str]] = []

    def coordinator_node(state: dict) -> dict:
        failures = state.get("explore_failures", [])
        messages = sorted(failure.message for failure in failures)
        coordinator_calls.append(messages)
        if failures:
            return {
                "status": "FAILED",
                "next_action": "FAILED",
                "failure": make_failure("MODEL", "；".join(messages)),
            }
        return {
            "next_action": "EXPLORE",
            "explore_focuses": ["成功分支", "失败一", "失败二"],
            "explore_titles": ["成功分支", "失败一", "失败二"],
        }

    def explore_node(state: dict) -> dict:
        focus = state["explore_focus"]
        if focus == "成功分支":
            return {"explore_reports": [make_report()]}
        return {
            "explore_failures": [make_failure("MODEL", f"{focus}异常")]
        }

    graph = StateGraph(ResolverState)
    graph.add_node("coordinator", coordinator_node)
    graph.add_node("explore", explore_node)
    graph.add_edge(START, "coordinator")
    graph.add_conditional_edges(
        "coordinator",
        route_after_coordinator,
        {
            "CODE": END,
            "FINISH": END,
            "FAILED": END,
        },
    )
    graph.add_edge("explore", "coordinator")

    result = graph.compile().invoke(
        {
            "status": "RUNNING",
            "explore_reports": [],
            "explore_failures": [],
        }
    )

    assert coordinator_calls == [
        [],
        ["失败一异常", "失败二异常"],
    ]
    assert len(result["explore_reports"]) == 1
    assert result["status"] == "FAILED"
    assert result["next_action"] == "FAILED"
    assert route_after_coordinator(
        {
            "status": "FAILED",
            "next_action": "EXPLORE",
            "explore_focuses": ["不会执行"],
        }
    ) == "FAILED"
