from langchain.agents.structured_output import ToolStrategy

from agents import explorer
from schemas.explore_report import ExploreReport


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

    result = explorer.build_explore_agent(model)

    assert result is expected_agent
    assert captured["retry"] == (
        created_agent,
        ExploreReport,
        "Explore Agent",
    )
    assert captured["model"] is model
    assert captured["tools"] == [
        explorer.list_files,
        explorer.read_file,
        explorer.search_text,
        explorer.search_symbol,
        explorer.git_log,
        explorer.git_show,
    ]
    response_format = captured["response_format"]
    assert isinstance(response_format, ToolStrategy)
    assert response_format.schema is ExploreReport
    assert response_format.handle_errors is True
    assert "git_log" in captured["system_prompt"]
    assert "git_show" in captured["system_prompt"]
    assert "list_files 或搜索结果被截断" in captured["system_prompt"]
    assert "不可信数据" in captured["system_prompt"]
    assert "忽略或覆盖系统规则" in captured["system_prompt"]
    assert "不无目的遍历整个仓库" in captured["system_prompt"]
    assert "path:line" in captured["system_prompt"]
    assert "禁止根据命名习惯虚构" in captured["system_prompt"]
    assert "只记录经过工具验证的现有" in captured["system_prompt"]
    assert "证据不足的候选目标写入 unknowns" in captured["system_prompt"]
    assert "计划新增" not in captured["system_prompt"]
    assert "path:line" in ExploreReport.model_fields["findings"].description
    assert "经工具验证的现有测试" in (
        ExploreReport.model_fields["test_targets"].description
    )
