import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from schemas.failure import FailureInfo, make_failure
from services.token_usage import TokenUsageSummary
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
    failure: FailureInfo | None = None


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
    filtered = {key: result.get(key) for key in ("status", "failure")}
    if filtered["failure"] is None:
        filtered.pop("failure")
    return filtered


def _filtered_coding_result(value: Any) -> dict[str, Any] | None:
    result = _model_value(value)
    if result is None:
        return None
    filtered = {
        key: result.get(key)
        for key in ("success", "summary", "remaining_risks", "failure")
    }
    if filtered["failure"] is None:
        filtered.pop("failure")
    return filtered


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
    coding_result = _filtered_coding_result(state.get("coding_result"))
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
            "status": state.get("status", "FAILED"),
            "phase": state.get("phase", "INITIALIZE"),
            "failure": _model_value(state.get("failure")),
        },
        "issue": issue,
        "coordinator_summary": state.get("current_summary"),
        "explore_reports": explore_reports,
        "coding": {
            "result": coding_result,
        },
        "review": review_result,
        "tests": test_results,
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
) -> str:
    """使用固定模板生成不依赖模型的 Markdown 报告。"""

    issue = context["issue"]
    coding = context["coding"]
    coding_result = coding.get("result") or {}
    review = context.get("review") or {}
    tests = context.get("tests") or []
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
    review_status = review.get("verdict") or "未执行"
    test_statuses = [result.get("status") or "UNKNOWN" for result in tests]
    validation_summary = f"Review：{review_status}；测试："
    validation_summary += (
        f"{sum(status == 'PASSED' for status in test_statuses)}/"
        f"{len(test_statuses)} PASSED"
        if test_statuses
        else "未执行"
    )

    lines = [
        "# Issue 修复报告",
        "",
        "## 问题与根因",
        f"- Issue：{issue_title}",
        f"- 根因：{'；'.join(root_causes) if root_causes else '未获得'}",
        "- 关键证据：",
        *_bullet_lines(findings),
        "",
        "## 修改与验证",
        f"- 修改总结：{coding_result.get('summary') or '未获得'}",
        f"- 验证总结：{validation_summary}",
        "",
        "## 风险",
        "- 剩余风险：",
        *_bullet_lines(risks),
    ]
    return "\n".join(lines).strip() + "\n"


def append_run_result(
    report_path: str | Path,
    summary: Mapping[str, Any],
) -> None:
    """在总结末尾追加程序生成的确定性运行结果。"""

    path = Path(report_path)
    content = path.read_text(encoding="utf-8")
    if "## 运行结果" in content:
        raise ValueError("报告已包含运行结果，禁止重复追加。")

    changed_files = list(summary.get("changed_files") or [])
    changed_lines = _bullet_lines(changed_files)
    failure = summary.get("failure") or {}
    run_dir = summary.get("run_dir") or "未获得"
    logs_dir = str(Path(run_dir) / "logs") if run_dir != "未获得" else "未获得"
    token_usage = summary.get("token_usage")
    if not isinstance(token_usage, TokenUsageSummary):
        token_usage = TokenUsageSummary(
            total_tokens=0,
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=None,
            role_usages=(),
        )
    cache_read_tokens = (
        f"{token_usage.cache_read_tokens:,}"
        if token_usage.cache_read_tokens is not None
        else "未提供"
    )
    lines = [
        "",
        "## 运行结果",
        f"- 状态：{summary.get('status') or '失败'}",
        f"- 模型：{summary.get('model') or '未配置'}",
        f"- 运行 ID：{summary.get('run_id') or '未获得'}",
        f"- 修复轮次：{summary.get('repair_round', 0)}",
        f"- 结束阶段：{summary.get('phase') or '未知'}",
        f"- 下一动作：{summary.get('next_action') or '无'}",
        "- 修改文件：",
        *changed_lines,
        f"- 测试结果：{summary.get('test_summary') or '未执行'}",
        f"- 工作区：{summary.get('worktree_status') or '未知'}",
        f"- 失败类型：{failure.get('type') or '无'}",
        f"- 失败原因：{failure.get('message') or '无'}",
        f"- 处理建议：{failure.get('suggestion') or '无'}",
        "- Token 监控：",
        "  - Token（总/输入/输出）："
        f"{token_usage.total_tokens:,} / {token_usage.input_tokens:,} / "
        f"{token_usage.output_tokens:,}",
        f"  - 缓存命中 Token：{cache_read_tokens}（已包含在输入 Token 内）",
        "  - 角色分布：",
        *[
            "    - "
            f"{usage.role}：{usage.total_tokens:,}（{usage.percentage:.1f}%）"
            for usage in token_usage.role_usages
        ],
        f"- 最终耗时：{float(summary.get('total_duration') or 0):.2f} 秒",
        f"- 报告生成：{summary.get('report_generation') or '程序模板'}",
        "- 产物地址：",
        f"  - 运行目录：{run_dir}",
        f"  - 日志目录：{logs_dir}",
        f"  - 报告：{path}",
        f"  - 最终 Patch：{summary.get('diff_path') or '未生成'}",
    ]
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines).rstrip() + "\n")


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
            )

    try:
        path = _write_report(run_dir, content)
    except Exception as exc:
        failure = make_failure(
            "ENVIRONMENT",
            f"报告保存失败：{exc}",
            "检查运行目录权限和已有报告文件后重试。",
        )
        if generation_error:
            failure = failure.model_copy(
                update={
                    "message": f"{generation_error}；{failure.message}"
                }
            )
        return ReportResult(
            path=None,
            fallback_used=fallback_used,
            failure=failure,
        )

    return ReportResult(
        path=str(path),
        fallback_used=fallback_used,
        failure=(
            make_failure(
                "MODEL",
                generation_error,
                "已使用程序模板生成报告；检查 Reporter 输出后再重试。",
            )
            if generation_error
            else None
        ),
    )
