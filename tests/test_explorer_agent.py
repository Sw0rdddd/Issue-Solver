from langchain.agents.structured_output import ToolStrategy

from agents import explorer
from prompts.explorer import build_explore_input
from schemas.evidence_digest import EvidenceDigest
from schemas.explore_report import ExploreReport
from schemas.issue_specification import IssueSpec
from services.tool_history import ToolHistoryWindowMiddleware


def test_build_explore_agent_uses_tool_strategy(monkeypatch) -> None:
    captured: dict[str, object] = {}
    created_agent = object()
    expected_agent = object()

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return created_agent

    def fake_retry(agent, response_type, *, agent_name):
        captured["retry"] = (agent, response_type, agent_name)
        return expected_agent

    monkeypatch.setattr(explorer, "create_agent", fake_create_agent)
    monkeypatch.setattr(
        explorer,
        "with_agent_structured_output_retry",
        fake_retry,
    )
    model = object()

    result = explorer.build_explore_agent(model, "C:/repo")

    assert result is expected_agent
    assert captured["retry"] == (
        created_agent,
        ExploreReport,
        "Explore Agent",
    )
    assert captured["model"] is model
    tools = {item.name: item for item in captured["tools"]}
    assert set(tools) == {
        "list_files",
        "read_file",
        "search_text",
        "search_symbol",
        "git_log",
        "git_show",
    }
    assert all(
        "repo_path" not in item.args_schema.model_fields
        for item in tools.values()
    )
    response_format = captured["response_format"]
    assert isinstance(response_format, ToolStrategy)
    assert response_format.schema is ExploreReport
    assert response_format.handle_errors is True
    middleware = captured["middleware"]
    assert len(middleware) == 1
    assert isinstance(middleware[0], ToolHistoryWindowMiddleware)
    assert "git_log" in captured["system_prompt"]
    assert "git_show" in captured["system_prompt"]
    assert "list_files 或搜索结果截断" in captured["system_prompt"]
    assert "调查流程" in captured["system_prompt"]
    assert "报告契约" in captured["system_prompt"]
    assert "返回前自检" in captured["system_prompt"]
    assert "只可来自真实工具输出" in captured["system_prompt"]
    assert "工具已绑定当前仓库" in captured["system_prompt"]
    assert "path:line" in captured["system_prompt"]
    assert "禁止根据命名习惯虚构" in captured["system_prompt"]
    assert "仅记录经过工具验证的既有" in captured["system_prompt"]
    assert "证据不足的候选目标或自然语言场景写入 unknowns" in (
        captured["system_prompt"]
    )
    assert "本次 focus 是唯一调查范围" in captured["system_prompt"]
    assert "Few-shot" in captured["system_prompt"]
    assert "示例一——证据不足" in captured["system_prompt"]
    assert "示例二——已读到源码和测试" in captured["system_prompt"]
    assert "绝不可照抄" in captured["system_prompt"]
    assert "root_cause\":\"\"" in captured["system_prompt"]
    assert captured["system_prompt"].rstrip().endswith("\"unknowns\":[]}")
    assert "计划新增" not in captured["system_prompt"]
    assert "path:line" in ExploreReport.model_fields["findings"].description
    assert "经工具和 read_file 验证的既有" in (
        ExploreReport.model_fields["test_targets"].description
    )


def test_explore_tools_bind_repo_path(monkeypatch) -> None:
    captured: list[dict] = []
    monkeypatch.setattr(
        explorer.git_log,
        "func",
        lambda **payload: captured.append(payload) or "history",
    )
    tools = {item.name: item for item in explorer.build_explore_tools("C:/repo")}

    assert tools["git_log"].invoke({"path": "src", "limit": 2}) == "history"
    assert captured == [
        {
            "repo_path": "C:/repo",
            "path": "src",
            "limit": 2,
        }
    ]


def test_explore_input_uses_bound_repo_context() -> None:
    content = build_explore_input(
        issue=IssueSpec(title="查询失败", body="应返回空列表"),
        focus="定位查询逻辑",
        evidence_digest=EvidenceDigest(
            source_report_count=1,
            root_cause="src/query.py:8 未处理空值",
            key_evidence=["src/query.py:8 直接使用空值"],
            relevant_files=["src/query.py"],
        ),
    )

    assert "所有工具已固定在当前仓库" in content
    assert "repo_path" not in content
    assert "当前 EvidenceDigest" in content
    assert "未处理空值" in content
