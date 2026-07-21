from pathlib import Path

from langchain_core.runnables import RunnableLambda

from schemas.coding_result import CodingResult
from schemas.explore_report import ExploreReport
from schemas.failure import make_failure
from schemas.issue_specification import IssueSpec
from schemas.review_result import ReviewResult
from schemas.test_result import TestResult as SolverTestResult
from services.report import (
    append_run_result,
    build_report_context,
    create_run_report,
)


def valid_report_markdown() -> str:
    return """# Issue 修复报告

## 问题与根因
- Issue：搜索忽略大小写
- 根因：src/search.py:8 未统一大小写
- 关键证据：
  - src/search.py:8 直接比较原始字符串

## 修改与验证
- 修改总结：统一查询和标题的大小写
- 验证总结：Review 和测试均通过

## 风险
- 剩余风险：
  - 未获得"""


def make_state() -> dict:
    return {
        "run_id": "run_test",
        "status": "FINISHED",
        "phase": "FINALIZE",
        "repair_round": 1,
        "repo_path": "E:/repo",
        "base_commit": "abc123",
        "issue": IssueSpec(
            title="搜索忽略大小写",
            body="查询大小写不同时无法匹配",
            expected_behavior="忽略大小写",
            actual_behavior="返回空列表",
            acceptance_criteria=["大小写不同也能匹配"],
        ),
        "current_summary": "修复已通过审查和测试",
        "explore_reports": [
            ExploreReport(
                focus="定位搜索逻辑",
                relevant_files=["src/search.py"],
                findings=["src/search.py:8 直接比较原始字符串"],
                root_cause="src/search.py:8 未统一大小写",
                test_targets=["tests/test_search.py"],
                unknowns=[],
            )
        ],
        "coding_result": CodingResult(
            success=True,
            changed_files=["src/search.py"],
            summary="统一查询和标题的大小写",
            diff_path=None,
            validation=["已检查 Diff"],
            remaining_risks=[],
        ),
        "changed_files": ["src/search.py"],
        "review_result": ReviewResult(
            verdict="APPROVE",
            issues=[],
            suggestions=[],
            remaining_risks=[],
        ),
        "latest_test_results": [
            SolverTestResult(
                command="pytest -q tests/test_search.py",
                resolved_command=["E:/repo/.venv/python.exe", "-m", "pytest"],
                cwd="E:/repo",
                python_executable="E:/repo/.venv/python.exe",
                status="PASSED",
                exit_code=0,
                duration=0.25,
                stdout_path="E:/runs/stdout.log",
                stderr_path="E:/runs/stderr.log",
                output_tail="[stdout] 1 passed",
            )
        ],
        "diff_path": "E:/runs/run_test/diff.patch",
    }


def test_report_context_excludes_full_test_logs_and_resolved_command() -> None:
    context = build_report_context(
        make_state(),
        model_name="deepseek-reasoner",
        worktree_status="保留修改",
    )

    assert context["tests"] == [
        {
            "status": "PASSED",
        }
    ]
    assert context["run"] == {
        "status": "FINISHED",
        "phase": "FINALIZE",
        "failure": None,
    }
    assert context["coding"]["result"] == {
        "success": True,
        "summary": "统一查询和标题的大小写",
        "remaining_risks": [],
    }
    assert "src/search.py:8" in context["explore_reports"][0]["root_cause"]
    rendered = str(context)
    assert "output_tail" not in rendered
    assert "stdout_path" not in rendered
    assert "resolved_command" not in rendered
    assert "run_test" not in rendered
    assert "deepseek-reasoner" not in rendered
    assert "diff.patch" not in rendered


def test_create_run_report_writes_model_markdown(tmp_path: Path) -> None:
    captured: list[object] = []
    markdown = valid_report_markdown()

    def invoke(messages: object) -> str:
        captured.append(messages)
        return markdown

    result = create_run_report(
        run_dir=tmp_path,
        state=make_state(),
        model_name="deepseek-reasoner",
        worktree_status="保留修改",
        report_agent=RunnableLambda(invoke),
    )

    assert result.path == str(tmp_path / "report.md")
    assert result.fallback_used is False
    assert result.failure is None
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == markdown + "\n"
    assert len(captured) == 1
    messages = captured[0]
    assert "不能修改任何文件" in messages[0].content
    assert "状态、Token、耗时和产物地址由程序" in messages[0].content
    assert '"status": "PASSED"' in messages[1].content
    assert "pytest -q tests/test_search.py" not in messages[1].content
    assert "output_tail" not in messages[1].content


def test_create_run_report_falls_back_when_agent_fails(tmp_path: Path) -> None:
    def fail(_: object) -> str:
        raise RuntimeError("模型不可用")

    result = create_run_report(
        run_dir=tmp_path,
        state=make_state(),
        model_name="deepseek-reasoner",
        worktree_status="保留修改",
        report_agent=RunnableLambda(fail),
    )

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert result.fallback_used is True
    assert result.failure.type == "MODEL"
    assert result.failure.message == "模型不可用"
    assert "## 问题与根因" in report
    assert "## 运行结果" not in report
    assert "src/search.py:8 未统一大小写" in report


def test_create_run_report_falls_back_when_agent_returns_empty_text(
    tmp_path: Path,
) -> None:
    result = create_run_report(
        run_dir=tmp_path,
        state=make_state(),
        model_name="deepseek-reasoner",
        worktree_status="保留修改",
        report_agent=RunnableLambda(lambda _: "  "),
    )

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert result.fallback_used is True
    assert result.failure.message == "Reporter 返回了空文本。"
    assert "## 修改与验证" in report


def test_create_run_report_falls_back_when_agent_changes_template(
    tmp_path: Path,
) -> None:
    result = create_run_report(
        run_dir=tmp_path,
        state=make_state(),
        model_name="deepseek-reasoner",
        worktree_status="保留修改",
        report_agent=RunnableLambda(
            lambda _: "# 自定义报告\n\n模型自由发挥"
        ),
    )

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert result.fallback_used is True
    assert result.failure.message == "Reporter 未使用固定报告标题。"
    assert report.startswith("# Issue 修复报告\n")
    assert "## 风险" in report


def test_create_run_report_without_model_uses_fallback(tmp_path: Path) -> None:
    state = {
        "run_id": "run_failed",
        "status": "FAILED",
        "phase": "INITIALIZE",
        "issue_input": "修复环境",
        "failure": make_failure("ENVIRONMENT", "未发现虚拟环境"),
    }

    result = create_run_report(
        run_dir=tmp_path,
        state=state,
        model_name="deepseek-reasoner",
        worktree_status="未修改",
        report_agent=None,
    )

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert result.fallback_used is True
    assert result.failure is None
    assert "Issue：修复环境" in report
    assert "## 运行结果" not in report


def test_append_run_result_adds_deterministic_terminal_summary(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "report.md"
    report_path.write_text(valid_report_markdown() + "\n", encoding="utf-8")

    append_run_result(
        report_path,
        {
            "status": "失败",
            "model": "deepseek-reasoner",
            "run_id": "run_test",
            "repair_round": 1,
            "phase": "FINALIZE",
            "next_action": "FINISH",
            "changed_files": ["src/search.py"],
            "test_summary": "1/1 PASSED",
            "worktree_status": "保留修改",
            "total_tokens": 12345,
            "total_duration": 48.09,
            "report_generation": "模型",
            "failure": {
                "type": "SOLUTION",
                "message": "修复未完成",
                "suggestion": "检查修改后重试。",
            },
            "run_dir": str(tmp_path),
            "diff_path": str(tmp_path / "diff.patch"),
        },
    )

    report = report_path.read_text(encoding="utf-8")
    assert report.index("## 运行结果") > report.index("## 风险")
    assert "- 总 Token：12,345" in report
    assert "- 最终耗时：48.09 秒" in report
    assert "- 失败类型：SOLUTION" in report
    assert "- 失败原因：修复未完成" in report
    assert "- 处理建议：检查修改后重试。" in report
    assert f"  - 运行目录：{tmp_path}" in report
    assert f"  - 日志目录：{tmp_path / 'logs'}" in report
    assert f"  - 报告：{report_path}" in report
    assert f"  - 最终 Patch：{tmp_path / 'diff.patch'}" in report


def test_create_run_report_never_overwrites_existing_report(tmp_path: Path) -> None:
    report_path = tmp_path / "report.md"
    report_path.write_text("existing\n", encoding="utf-8")

    result = create_run_report(
        run_dir=tmp_path,
        state=make_state(),
        model_name="deepseek-reasoner",
        worktree_status="保留修改",
        report_agent=None,
    )

    assert result.path is None
    assert "禁止覆盖" in result.failure.message
    assert report_path.read_text(encoding="utf-8") == "existing\n"
