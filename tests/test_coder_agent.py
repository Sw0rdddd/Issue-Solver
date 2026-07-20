from langchain.agents.structured_output import ToolStrategy

from agents import coder
from prompts.coder import CODING_SYSTEM_PROMPT, build_coding_input
from schemas.coding_result import CodingResult
from schemas.coding_task import CodingTask
from schemas.issue_specification import IssueSpec


def test_build_coding_agent_uses_only_bound_coding_tools(monkeypatch) -> None:
    captured: dict[str, object] = {}
    expected_agent = object()
    context = object()
    apply_patch_tool = object()
    inspect_changes_tool = object()

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return expected_agent

    def fake_build_coding_tools(received_context):
        assert received_context is context
        return [apply_patch_tool, inspect_changes_tool]

    monkeypatch.setattr(coder, "create_agent", fake_create_agent)
    monkeypatch.setattr(coder, "build_coding_tools", fake_build_coding_tools)
    model = object()

    result = coder.build_coding_agent(model, context)

    assert result is expected_agent
    assert captured["model"] is model
    assert captured["tools"] == [
        coder.list_files,
        coder.read_file,
        coder.search_text,
        coder.search_symbol,
        apply_patch_tool,
        inspect_changes_tool,
    ]
    assert captured["system_prompt"] == CODING_SYSTEM_PROMPT
    assert captured["name"] == "coding_agent"
    response_format = captured["response_format"]
    assert isinstance(response_format, ToolStrategy)
    assert response_format.schema is CodingResult


def test_coding_prompt_defines_safe_iterative_workflow() -> None:
    assert "唯一允许的写入方式是 apply_patch" in CODING_SYSTEM_PROMPT
    assert "修改前" in CODING_SYSTEM_PROMPT
    assert "read_file" in CODING_SYSTEM_PROMPT
    assert "可以连续调用多次 apply_patch" in CODING_SYSTEM_PROMPT
    assert "必须调用 inspect_changes" in CODING_SYSTEM_PROMPT
    assert "不得执行测试" in CODING_SYSTEM_PROMPT
    assert "不得声称测试已经通过" in CODING_SYSTEM_PROMPT
    assert "diff_path 必须为 null" in CODING_SYSTEM_PROMPT
    assert "changed_files" in CODING_SYSTEM_PROMPT
    assert "list_files 或搜索工具提示结果被截断" in CODING_SYSTEM_PROMPT


def test_build_coding_input_contains_structured_context() -> None:
    task = CodingTask(
        objective="修复搜索",
        acceptance_criteria=["忽略大小写"],
        relevant_files=["search.py"],
        root_cause="使用了区分大小写的比较",
        allowed_scope=["search.py"],
        test_targets=["tests/test_search.py"],
    )

    content = build_coding_input(
        repo_path="C:/repo",
        issue=IssueSpec(title="搜索失败", body="大小写不同无法匹配"),
        coding_task=task,
        explore_reports=[],
        current_summary="根因明确",
    )

    assert "C:/repo" in content
    assert "搜索失败" in content
    assert "修复搜索" in content
    assert "根因明确" in content
