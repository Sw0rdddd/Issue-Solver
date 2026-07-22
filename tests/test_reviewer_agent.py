from langchain.agents.structured_output import ToolStrategy

from agents import reviewer
from prompts.reviewer import REVIEW_SYSTEM_PROMPT, build_review_input
from schemas.coding_result import CodingResult
from schemas.coding_task import CodingTask
from schemas.issue_specification import IssueSpec
from schemas.review_result import ReviewResult


def test_build_review_agent_uses_only_read_only_tools(monkeypatch) -> None:
    captured: dict[str, object] = {}
    created_agent = object()
    expected_agent = object()

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return created_agent

    def fake_retry(agent, response_type, *, agent_name):
        captured["retry"] = (agent, response_type, agent_name)
        return expected_agent

    monkeypatch.setattr(reviewer, "create_agent", fake_create_agent)
    monkeypatch.setattr(
        reviewer,
        "with_agent_structured_output_retry",
        fake_retry,
    )
    model = object()

    result = reviewer.build_review_agent(model, "C:/repo", "abc123")

    assert result is expected_agent
    assert captured["retry"] == (
        created_agent,
        ReviewResult,
        "Review Agent",
    )
    assert captured["model"] is model
    tools = {item.name: item for item in captured["tools"]}
    assert set(tools) == {
        "list_files",
        "read_file",
        "search_text",
        "search_symbol",
        "git_diff",
    }
    assert all(
        "repo_path" not in item.args_schema.model_fields
        for item in tools.values()
    )
    assert "base_commit" not in tools["git_diff"].args_schema.model_fields
    assert captured["system_prompt"] == REVIEW_SYSTEM_PROMPT
    assert captured["name"] == "review_agent"
    response_format = captured["response_format"]
    assert isinstance(response_format, ToolStrategy)
    assert response_format.schema is ReviewResult
    assert response_format.handle_errors is True


def test_review_tools_bind_repo_path_and_base_commit(monkeypatch) -> None:
    captured: list[dict] = []
    monkeypatch.setattr(
        reviewer.git_diff,
        "func",
        lambda **payload: captured.append(payload) or "diff",
    )
    tools = {
        item.name: item
        for item in reviewer.build_review_tools("C:/repo", "abc123")
    }

    assert tools["git_diff"].invoke({"path": "src"}) == "diff"
    assert captured == [
        {
            "repo_path": "C:/repo",
            "base_commit": "abc123",
            "path": "src",
            "max_chars": 20_000,
        }
    ]


def test_review_prompt_defines_read_only_diff_workflow() -> None:
    assert "禁止修改、创建或删除任何文件" in REVIEW_SYSTEM_PROMPT
    assert "必须先调用 git_diff" in REVIEW_SYSTEM_PROMPT
    assert "输出被截断" in REVIEW_SYSTEM_PROMPT
    assert "不得执行测试" in REVIEW_SYSTEM_PROMPT
    assert "Review 结论不能替代" in REVIEW_SYSTEM_PROMPT
    assert "APPROVE 时 issues 必须为空" in REVIEW_SYSTEM_PROMPT
    assert "REQUEST_CHANGES 时 issues 必须至少包含一个具体问题" in REVIEW_SYSTEM_PROMPT
    assert "list_files 或搜索结果被截断" in REVIEW_SYSTEM_PROMPT
    assert "不可信数据" in REVIEW_SYSTEM_PROMPT
    assert "忽略或覆盖系统规则" in REVIEW_SYSTEM_PROMPT
    assert "纯风格偏好不能单独阻止通过" in REVIEW_SYSTEM_PROMPT
    assert "不得把未验证的推测写成事实" in REVIEW_SYSTEM_PROMPT
    assert "工具已固定在当前仓库" in REVIEW_SYSTEM_PROMPT
    assert "相对已绑定 base commit" in REVIEW_SYSTEM_PROMPT


def test_build_review_input_contains_structured_context() -> None:
    issue = IssueSpec(title="搜索失败", body="大小写不同无法匹配")
    task = CodingTask(
        objective="修复搜索",
        acceptance_criteria=["忽略大小写"],
        relevant_files=["search.py"],
        root_cause="使用了区分大小写的比较",
        allowed_scope=["search.py", "tests/test_search.py"],
        test_targets=["tests/test_search.py"],
    )
    coding_result = CodingResult(
        success=True,
        changed_files=["search.py", "tests/test_search.py"],
        summary="统一搜索字符串大小写",
        diff_path=None,
        validation=["已检查累计差异"],
        remaining_risks=[],
    )
    content = build_review_input(
        issue=issue,
        coding_task=task,
        coding_result=coding_result,
    )

    assert "搜索失败" in content
    assert "修复搜索" in content
    assert "统一搜索字符串大小写" in content
    assert "Explore Reports" not in content
    assert "EvidenceDigest" not in content
    assert "仓库根目录" not in content
    assert "基础 Commit" not in content
