from langchain.agents.structured_output import ToolStrategy

from agents import reviewer
from prompts.reviewer import REVIEW_SYSTEM_PROMPT, build_review_input
from schemas.coding_result import CodingResult
from schemas.coding_task import CodingTask
from schemas.explore_report import ExploreReport
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

    result = reviewer.build_review_agent(model)

    assert result is expected_agent
    assert captured["retry"] == (
        created_agent,
        ReviewResult,
        "Review Agent",
    )
    assert captured["model"] is model
    assert captured["tools"] == [
        reviewer.list_files,
        reviewer.read_file,
        reviewer.search_text,
        reviewer.search_symbol,
        reviewer.git_diff,
    ]
    assert captured["system_prompt"] == REVIEW_SYSTEM_PROMPT
    assert captured["name"] == "review_agent"
    response_format = captured["response_format"]
    assert isinstance(response_format, ToolStrategy)
    assert response_format.schema is ReviewResult
    assert response_format.handle_errors is True


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
    report = ExploreReport(
        focus="定位搜索逻辑",
        relevant_files=["search.py"],
        relevant_symbols=["search_tasks"],
        findings=["标题直接使用区分大小写的包含判断"],
        root_cause="查询和标题未进行大小写归一化",
        test_targets=["tests/test_search.py"],
        unknowns=[],
    )

    content = build_review_input(
        repo_path="C:/repo",
        base_commit="abc123",
        issue=issue,
        coding_task=task,
        coding_result=coding_result,
        explore_reports=[report],
        current_summary="编码完成，等待审查",
    )

    assert "C:/repo" in content
    assert "abc123" in content
    assert "搜索失败" in content
    assert "修复搜索" in content
    assert "统一搜索字符串大小写" in content
    assert "定位搜索逻辑" in content
    assert "编码完成，等待审查" in content
