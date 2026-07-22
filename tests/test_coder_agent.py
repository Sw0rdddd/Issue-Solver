from types import SimpleNamespace

from langchain.agents.structured_output import ToolStrategy

from agents import coder
from prompts.coder import CODING_SYSTEM_PROMPT, build_coding_input
from schemas.coding_result import CodingResult
from schemas.coding_task import CodingTask
from schemas.evidence_digest import EvidenceDigest
from schemas.issue_specification import IssueSpec


def test_build_coding_agent_uses_only_bound_coding_tools(monkeypatch) -> None:
    captured: dict[str, object] = {}
    expected_agent = object()
    context = SimpleNamespace(repo_root="C:/repo")
    apply_patch_tool = object()
    inspect_changes_tool = object()
    read_tools = [object(), object(), object(), object()]

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return expected_agent

    def fake_build_coding_tools(received_context):
        assert received_context is context
        return [apply_patch_tool, inspect_changes_tool]

    monkeypatch.setattr(coder, "create_agent", fake_create_agent)
    monkeypatch.setattr(coder, "build_coding_tools", fake_build_coding_tools)
    monkeypatch.setattr(
        coder,
        "build_coding_read_tools",
        lambda received_context: (
            read_tools if received_context is context else []
        ),
    )
    model = object()

    result = coder.build_coding_agent(model, context)

    assert result is expected_agent
    assert captured["model"] is model
    assert captured["tools"] == [
        *read_tools,
        apply_patch_tool,
        inspect_changes_tool,
    ]
    assert captured["system_prompt"] == CODING_SYSTEM_PROMPT
    assert captured["name"] == "coding_agent"
    response_format = captured["response_format"]
    assert isinstance(response_format, ToolStrategy)
    assert response_format.schema is CodingResult
    assert response_format.handle_errors is True


def test_coding_prompt_defines_safe_iterative_workflow() -> None:
    assert "唯一允许的写入方式是 apply_patch" in CODING_SYSTEM_PROMPT
    assert "修改前" in CODING_SYSTEM_PROMPT
    assert "read_file" in CODING_SYSTEM_PROMPT
    assert "可以连续调用多次 apply_patch" in CODING_SYSTEM_PROMPT
    assert "Patch 成功后立即调用 inspect_changes" in CODING_SYSTEM_PROMPT
    assert "不得执行测试" in CODING_SYSTEM_PROMPT
    assert "不得执行测试或声称测试通过" in CODING_SYSTEM_PROMPT
    assert "成功时 diff_path 和 failure 为 null" in CODING_SYSTEM_PROMPT
    assert "changed_files" in CODING_SYSTEM_PROMPT
    assert "list_files 或搜索结果被截断" in CODING_SYSTEM_PROMPT
    assert "不可信数据" in CODING_SYSTEM_PROMPT
    assert "success=true 只表示" in CODING_SYSTEM_PROMPT
    assert "不代表 Review 或测试通过" in CODING_SYSTEM_PROMPT
    assert "INPUT 参数错误应修正，不得报告为 ENVIRONMENT" in (
        CODING_SYSTEM_PROMPT
    )
    assert "最多允许 10 次 Patch 尝试" in CODING_SYSTEM_PROMPT
    assert "后续 Patch 基于当前累计工作区" in CODING_SYSTEM_PROMPT
    assert "%2B" in CODING_SYSTEM_PROMPT
    assert "全角字符" in CODING_SYSTEM_PROMPT
    assert "禁止 Windows 盘符" in CODING_SYSTEM_PROMPT
    assert "仓库绝对路径" in CODING_SYSTEM_PROMPT
    assert "diff --git a/path/to/file.py b/path/to/file.py" in (
        CODING_SYSTEM_PROMPT
    )
    assert "逐项核对 CodingTask.acceptance_criteria" in (
        CODING_SYSTEM_PROMPT
    )
    assert "仍有可可靠完成的项目时继续修改" in CODING_SYSTEM_PROMPT
    assert "不得仅因任务尚未完成就返回 success=false" in (
        CODING_SYSTEM_PROMPT
    )
    assert "无法在 allowed_scope 内可靠完成时返回失败" in (
        CODING_SYSTEM_PROMPT
    )
    assert "不得扩大范围" in CODING_SYSTEM_PROMPT
    assert "不重复等价 Patch" in CODING_SYSTEM_PROMPT
    assert "ENVIRONMENT、LIMIT 或 INTERNAL 应停止无意义重试" in (
        CODING_SYSTEM_PROMPT
    )
    assert "不代表 Review 或测试通过" in (
        CodingResult.model_fields["success"].description
    )


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
        evidence_digest=EvidenceDigest(
            source_report_count=1,
            root_cause="search.py:8 比较区分大小写",
            key_evidence=["search.py:8 直接比较原始字符串"],
            relevant_files=["search.py"],
        ),
    )

    assert "C:/repo" in content
    assert "搜索失败" in content
    assert "修复搜索" in content
    assert "EvidenceDigest" in content
    assert "直接比较原始字符串" in content


def test_coding_read_tools_bind_repo_path(tmp_path) -> None:
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n", encoding="utf-8")
    context = SimpleNamespace(repo_root=tmp_path)

    tools = {
        item.name: item for item in coder.build_coding_read_tools(context)
    }

    assert "repo_path" not in tools["read_file"].args_schema.model_fields
    result = tools["read_file"].invoke({"path": "sample.py"})
    assert "value = 1" in result
