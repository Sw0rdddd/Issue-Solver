from langchain.agents.structured_output import ToolStrategy

from agents import explorer
from schemas.explore_report import ExploreReport


def test_build_explore_agent_uses_tool_strategy(monkeypatch) -> None:
    captured: dict[str, object] = {}
    expected_agent = object()

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return expected_agent

    monkeypatch.setattr(explorer, "create_agent", fake_create_agent)
    model = object()

    result = explorer.build_explore_agent(model)

    assert result is expected_agent
    assert captured["model"] is model
    assert captured["tools"] == [
        explorer.list_files,
        explorer.read_file,
        explorer.search_text,
        explorer.search_symbol,
        explorer.git_log,
    ]
    response_format = captured["response_format"]
    assert isinstance(response_format, ToolStrategy)
    assert response_format.schema is ExploreReport
