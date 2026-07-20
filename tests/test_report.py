from pathlib import Path

from langchain_core.runnables import RunnableLambda

from schemas.coding_result import CodingResult
from schemas.explore_report import ExploreReport
from schemas.issue_specification import IssueSpec
from schemas.review_result import ReviewResult
from schemas.test_result import TestResult as SolverTestResult
from services.report import build_report_context, create_run_report


def valid_report_markdown() -> str:
    return """# Issue 修复报告

## 运行结果
- 状态：FINISHED
- 运行 ID：run_test
- 模型：deepseek-reasoner
- 结束阶段：FINALIZE
- 修复轮次：1
- 工作区：保留修改
- 失败原因：无

## 问题与根因
- Issue：搜索忽略大小写
- 根因：src/search.py:8 未统一大小写
- 关键证据：
  - src/search.py:8 直接比较原始字符串

## 修改内容
- 编码摘要：统一查询和标题的大小写
- 修改文件：
  - src/search.py

## 验证结果
- Review：APPROVE
- 测试：
  - `pytest -q tests/test_search.py`：PASSED，退出码 0，0.25 秒

## 风险与交付物
- 剩余风险：
  - 未获得
- 最终 Patch：E:/runs/run_test/diff.patch
- 报告生成：模型"""


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
            "command": "pytest -q tests/test_search.py",
            "status": "PASSED",
            "exit_code": 0,
            "duration": 0.25,
        }
    ]
    assert "src/search.py:8" in context["explore_reports"][0]["root_cause"]
    rendered = str(context)
    assert "output_tail" not in rendered
    assert "stdout_path" not in rendered
    assert "resolved_command" not in rendered


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
    assert result.error is None
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == markdown + "\n"
    assert len(captured) == 1
    messages = captured[0]
    assert "不能修改任何文件" in messages[0].content
    assert "pytest -q tests/test_search.py" in messages[1].content
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
    assert result.error == "模型不可用"
    assert "## 问题与根因" in report
    assert "报告生成：程序模板（模型不可用）" in report
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
    assert result.error == "Reporter 返回了空文本。"
    assert "## 验证结果" in report


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
    assert result.error == "Reporter 未使用固定报告标题。"
    assert report.startswith("# Issue 修复报告\n")
    assert "- 报告生成：程序模板" in report


def test_create_run_report_without_model_uses_fallback(tmp_path: Path) -> None:
    state = {
        "run_id": "run_failed",
        "status": "FAILED",
        "phase": "INITIALIZE",
        "issue_input": "修复环境",
        "error": "未发现虚拟环境",
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
    assert result.error is None
    assert "状态：FAILED" in report
    assert "工作区：未修改" in report
    assert "未发现虚拟环境" in report


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
    assert "禁止覆盖" in result.error
    assert report_path.read_text(encoding="utf-8") == "existing\n"
