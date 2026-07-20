import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from prompts.reporter import (
    REPORT_REQUIRED_LABELS,
    REPORT_SECTION_HEADINGS,
    REPORT_SYSTEM_PROMPT,
    build_report_input,
)


REPORT_FILENAME = "report.md"


@dataclass(frozen=True)
class ReportResult:
    path: str | None
    fallback_used: bool
    error: str | None = None


def _model_value(value: Any) -> dict[str, Any] | None:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    return None


def _filtered_explore_report(value: Any) -> dict[str, Any] | None:
    report = _model_value(value)
    if report is None:
        return None
    return {
        key: report.get(key)
        for key in (
            "focus",
            "relevant_files",
            "findings",
            "root_cause",
            "unknowns",
        )
    }


def _filtered_test_result(value: Any) -> dict[str, Any] | None:
    result = _model_value(value)
    if result is None:
        return None
    return {
        key: result.get(key)
        for key in ("command", "status", "exit_code", "duration")
    }


def build_report_context(
    state: Mapping[str, Any],
    *,
    model_name: str | None,
    worktree_status: str | None,
) -> dict[str, Any]:
    """从最终 State 中筛选 Reporter 可以看到的最小上下文。"""

    issue = _model_value(state.get("issue"))
    if issue is None:
        issue = {"raw_input": state.get("issue_input", "")}

    explore_reports = [
        filtered
        for report in (state.get("explore_reports") or [])
        if (filtered := _filtered_explore_report(report)) is not None
    ]
    coding_result = _model_value(state.get("coding_result"))
    review_result = _model_value(state.get("review_result"))
    test_values = state.get("latest_test_results")
    if test_values is None:
        test_values = state.get("test_results") or []
    test_results = [
        filtered
        for result in test_values
        if (filtered := _filtered_test_result(result)) is not None
    ]

    return {
        "run": {
            "run_id": state.get("run_id"),
            "model": model_name or "未配置",
            "status": state.get("status", "FAILED"),
            "phase": state.get("phase", "INITIALIZE"),
            "repair_round": state.get(
                "repair_round",
                state.get("cycle", 0),
            ),
            "repo_path": state.get("repo_path"),
            "base_commit": state.get("base_commit"),
            "next_action": state.get("next_action"),
            "error": state.get("error"),
            "worktree_status": worktree_status or "未知",
        },
        "issue": issue,
        "coordinator_summary": state.get("current_summary"),
        "explore_reports": explore_reports,
        "coding": {
            "result": coding_result,
            "changed_files": list(state.get("changed_files") or []),
        },
        "review": review_result,
        "tests": test_results,
        "delivery": {
            "diff_path": state.get("diff_path"),
            "rollback_required": state.get("rollback_required", False),
            "rollback_prompt_required": state.get(
                "rollback_prompt_required",
                False,
            ),
        },
    }


def _bullet_lines(values: list[Any]) -> list[str]:
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    return [f"  - {value}" for value in cleaned] or ["  - 未获得"]


def _validate_report_format(content: str) -> None:
    lines = content.strip().splitlines()
    if not lines or lines[0] != "# Issue 修复报告":
        raise ValueError("Reporter 未使用固定报告标题。")

    headings = [line for line in lines if line.startswith("## ")]
    if headings != list(REPORT_SECTION_HEADINGS):
        raise ValueError("Reporter 未遵循固定章节顺序。")
    for label in REPORT_REQUIRED_LABELS:
        if not any(line.startswith(label) for line in lines):
            raise ValueError(f"Reporter 缺少固定字段：{label}")


def build_fallback_report(
    context: Mapping[str, Any],
    *,
    generation_error: str | None = None,
) -> str:
    """使用固定模板生成不依赖模型的 Markdown 报告。"""

    run = context["run"]
    issue = context["issue"]
    coding = context["coding"]
    coding_result = coding.get("result") or {}
    review = context.get("review") or {}
    tests = context.get("tests") or []
    delivery = context["delivery"]

    issue_title = issue.get("title") or issue.get("raw_input") or "未获得"
    root_causes = [
        report.get("root_cause")
        for report in context.get("explore_reports", [])
        if report.get("root_cause")
    ]
    findings = [
        finding
        for report in context.get("explore_reports", [])
        for finding in (report.get("findings") or [])
    ]
    risks = [
        *(coding_result.get("remaining_risks") or []),
        *(review.get("remaining_risks") or []),
    ]
    test_lines = [
        (
            f"  - `{result.get('command') or '未知命令'}`："
            f"{result.get('status') or 'UNKNOWN'}，"
            f"退出码 {result.get('exit_code', '未知')}，"
            f"{float(result.get('duration') or 0):.2f} 秒"
        )
        for result in tests
    ] or ["  - 未执行"]

    lines = [
        "# Issue 修复报告",
        "",
        "## 运行结果",
        f"- 状态：{run.get('status') or 'FAILED'}",
        f"- 运行 ID：{run.get('run_id') or '未获得'}",
        f"- 模型：{run.get('model') or '未配置'}",
        f"- 结束阶段：{run.get('phase') or '未知'}",
        f"- 修复轮次：{run.get('repair_round', 0)}",
        f"- 工作区：{run.get('worktree_status') or '未知'}",
        f"- 失败原因：{run.get('error') or '无'}",
        "",
        "## 问题与根因",
        f"- Issue：{issue_title}",
        f"- 根因：{'；'.join(root_causes) if root_causes else '未获得'}",
        "- 关键证据：",
        *_bullet_lines(findings),
        "",
        "## 修改内容",
        f"- 编码摘要：{coding_result.get('summary') or '未获得'}",
        "- 修改文件：",
        *_bullet_lines(coding.get("changed_files") or []),
        "",
        "## 验证结果",
        f"- Review：{review.get('verdict') or '未执行'}",
        "- 测试：",
        *test_lines,
        "",
        "## 风险与交付物",
        "- 剩余风险：",
        *_bullet_lines(risks),
        f"- 最终 Patch：{delivery.get('diff_path') or '未生成'}",
    ]
    if generation_error:
        lines.append(f"- 报告生成：程序模板（{generation_error}）")
    else:
        lines.append("- 报告生成：程序模板")
    return "\n".join(lines).strip() + "\n"


def _write_report(run_dir: str | Path, content: str) -> Path:
    directory = Path(run_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / REPORT_FILENAME
    if path.exists():
        raise FileExistsError(f"报告已存在，禁止覆盖：{path}")

    temporary = directory / f".{REPORT_FILENAME}.tmp"
    if temporary.exists():
        raise FileExistsError(f"报告临时文件已存在：{temporary}")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content.rstrip() + "\n")
        if path.exists():
            raise FileExistsError(f"报告已存在，禁止覆盖：{path}")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def create_run_report(
    *,
    run_dir: str | Path,
    state: Mapping[str, Any],
    model_name: str | None,
    worktree_status: str | None,
    report_agent: Runnable | None,
) -> ReportResult:
    """生成并保存报告；模型或保存失败都不会向调用方抛出异常。"""

    context = build_report_context(
        state,
        model_name=model_name,
        worktree_status=worktree_status,
    )
    fallback_used = report_agent is None
    generation_error: str | None = None

    if report_agent is None:
        content = build_fallback_report(context)
    else:
        try:
            content = report_agent.invoke(
                [
                    SystemMessage(content=REPORT_SYSTEM_PROMPT),
                    HumanMessage(content=build_report_input(context)),
                ]
            )
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Reporter 返回了空文本。")
            _validate_report_format(content)
        except Exception as exc:
            generation_error = str(exc)
            fallback_used = True
            content = build_fallback_report(
                context,
                generation_error=generation_error,
            )

    try:
        path = _write_report(run_dir, content)
    except Exception as exc:
        error = str(exc)
        if generation_error:
            error = f"{generation_error}；报告保存失败：{error}"
        return ReportResult(
            path=None,
            fallback_used=fallback_used,
            error=error,
        )

    return ReportResult(
        path=str(path),
        fallback_used=fallback_used,
        error=generation_error,
    )
