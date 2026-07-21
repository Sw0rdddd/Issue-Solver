import json
from pathlib import Path

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from config import Setting
from graph.routing import route_after_coordinator
from graph.state import ResolverState
from nodes.explore import build_explore_node
from schemas.explore_report import ExploreReport
from schemas.issue_specification import IssueSpec


class FakeExploreAgent:
    def __init__(
        self,
        result: dict | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict] = []
        self.configs: list[dict | None] = []

    def invoke(self, payload: dict, config: dict | None = None) -> dict:
        self.calls.append(payload)
        self.configs.append(config)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def make_issue() -> IssueSpec:
    return IssueSpec(
        title="空结果查询失败",
        body="查询没有结果时返回 500",
        expected_behavior="返回空列表",
        actual_behavior="返回 500",
        acceptance_criteria=["空结果返回空列表"],
    )


def make_report(focus: str) -> ExploreReport:
    return ExploreReport(
        focus=focus,
        relevant_files=["app.py"],
        relevant_symbols=["handle_request"],
        findings=["返回值可能为 None"],
        root_cause="直接遍历了 None",
        test_targets=["tests/test_app.py"],
        unknowns=[],
    )


def test_explore_node_uses_custom_focus_and_saves_next_report(
    tmp_path: Path,
) -> None:
    focus = "定位空结果处理逻辑及相关测试"
    previous_report = make_report("第一次探索")
    report = make_report(focus)
    agent = FakeExploreAgent(
        result={"structured_response": report},
    )
    node = build_explore_node(agent)

    result = node(
        {
            "issue": make_issue(),
            "repo_path": str(tmp_path),
            "run_dir": str(tmp_path),
            "explore_focus": focus,
            "current_summary": "Issue 已完成规范化",
            "explore_reports": [previous_report],
            "repair_round": 2,
            "explore_stage_call": 3,
            "explore_item_index": 2,
        }
    )

    assert result == {"explore_reports": [report]}
    assert len(agent.calls) == 1
    message = agent.calls[0]["messages"][0]
    assert message["role"] == "user"
    assert focus in message["content"]
    assert "Issue 已完成规范化" in message["content"]
    assert agent.configs == [
        {"recursion_limit": Setting().AGENT_RECURSION_LIMIT}
    ]

    report_path = tmp_path / "logs" / "explore_r02_s03_i02.json"
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved == {
        "stage": "EXPLORE",
        "repair_round": 2,
        "stage_call": 3,
        "index": 2,
        "payload": report.model_dump(),
    }


def test_explore_node_uses_default_focus_when_not_provided(
    tmp_path: Path,
) -> None:
    report = make_report("默认探索")
    agent = FakeExploreAgent(
        result={"structured_response": report},
    )
    node = build_explore_node(agent)

    result = node(
        {
            "issue": make_issue(),
            "repo_path": str(tmp_path),
            "run_dir": str(tmp_path),
        }
    )

    assert result == {"explore_reports": [report]}
    content = agent.calls[0]["messages"][0]["content"]
    assert "定位与 Issue 相关的代码、潜在根因和测试位置" in content
    assert "当前工作流摘要：\n暂无" in content
    assert (tmp_path / "logs" / "explore_r01_s01_i01.json").is_file()


def test_explore_node_rejects_missing_issue(tmp_path: Path) -> None:
    agent = FakeExploreAgent(result={})
    node = build_explore_node(agent)

    result = node(
        {
            "repo_path": str(tmp_path),
            "run_dir": str(tmp_path),
        }
    )

    assert result["explore_failures"][0].type == "INTERNAL"
    assert "State 中缺少规范化后的 Issue" in (
        result["explore_failures"][0].message
    )
    assert agent.calls == []


def test_explore_node_rejects_invalid_structured_response(
    tmp_path: Path,
) -> None:
    agent = FakeExploreAgent(
        result={"structured_response": {"focus": "定位异常"}},
    )
    node = build_explore_node(agent)

    result = node(
        {
            "issue": make_issue(),
            "repo_path": str(tmp_path),
            "run_dir": str(tmp_path),
        }
    )

    assert result["explore_failures"][0].type == "MODEL"
    assert "Explore Agent 未返回有效" in result["explore_failures"][0].message
    assert not list((tmp_path / "logs").glob("explore_*.json"))


def test_explore_node_returns_agent_error(tmp_path: Path) -> None:
    agent = FakeExploreAgent(error=RuntimeError("模型调用失败"))
    node = build_explore_node(agent)

    result = node(
        {
            "issue": make_issue(),
            "repo_path": str(tmp_path),
            "run_dir": str(tmp_path),
        }
    )

    assert result["explore_failures"][0].type == "MODEL"
    assert result["explore_failures"][0].message == "仓库探索失败：模型调用失败"
    assert not list((tmp_path / "logs").glob("explore_*.json"))


def test_explore_node_classifies_recursion_limit(tmp_path: Path) -> None:
    recursion_limit = Setting().AGENT_RECURSION_LIMIT
    agent = FakeExploreAgent(
        error=GraphRecursionError(
            f"Recursion limit of {recursion_limit} reached"
        )
    )
    node = build_explore_node(agent)

    result = node(
        {
            "issue": make_issue(),
            "repo_path": str(tmp_path),
            "run_dir": str(tmp_path),
            "agent_recursion_limit": recursion_limit,
        }
    )

    failure = result["explore_failures"][0]
    assert failure.type == "LIMIT"
    assert failure.message == (
        "仓库探索失败：Explore Agent 达到最大执行步数 "
        f"{recursion_limit}。"
    )


def test_explore_node_uses_send_coordinates(tmp_path: Path) -> None:
    report = make_report("并行探索")
    agent = FakeExploreAgent(
        result={"structured_response": report},
    )
    node = build_explore_node(agent)

    result = node(
        {
            "issue": make_issue(),
            "repo_path": str(tmp_path),
            "run_dir": str(tmp_path),
            "explore_focus": "并行探索",
            "repair_round": 2,
            "explore_stage_call": 4,
            "explore_item_index": 3,
        }
    )

    assert result == {"explore_reports": [report]}
    assert (tmp_path / "logs" / "explore_r02_s04_i03.json").is_file()
    assert not (
        tmp_path / "logs" / "explore_r02_s04_i01.json"
    ).exists()


def test_send_branches_write_unique_report_files(tmp_path: Path) -> None:
    report = make_report("并行探索")
    agent = FakeExploreAgent(
        result={"structured_response": report},
    )
    explore_node = build_explore_node(agent)

    def coordinator_node(state: dict) -> dict:
        if not state.get("explore_reports"):
            return {
                "next_action": "EXPLORE",
                "explore_focuses": ["入口", "根因", "测试"],
                "explore_stage_call": 1,
            }
        return {"next_action": "CODE"}

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
            "issue": make_issue(),
            "repo_path": str(tmp_path),
            "run_dir": str(tmp_path),
            "explore_reports": [],
            "explore_failures": [],
            "cycle": 0,
        }
    )

    assert len(agent.calls) == 3
    assert len(result["explore_reports"]) == 3
    assert sorted(
        path.name for path in (tmp_path / "logs").glob("explore_*.json")
    ) == [
        "explore_r01_s01_i01.json",
        "explore_r01_s01_i02.json",
        "explore_r01_s01_i03.json",
    ]
