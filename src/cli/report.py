from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from langchain_core.language_models import BaseChatModel

from agents.reporter import build_report_agent
from cli.terminal import TerminalReporter
from schemas.failure import make_failure
from services.openai_compatible_model import build_non_thinking_model
from services.report import ReportResult, create_run_report


@dataclass
class RunReportSession:
    """保证一次 CLI 运行最多生成一份最终报告。"""

    run_dir: Path
    model_name: str | None
    reporter: TerminalReporter
    completed: bool = False

    def finish(
        self,
        *,
        state: Mapping[str, Any],
        model: BaseChatModel | None,
        worktree_status: str | None,
    ) -> ReportResult:
        if self.completed:
            return ReportResult(
                path=None,
                fallback_used=True,
                failure=make_failure(
                    "INTERNAL",
                    "本次运行的报告已经生成。",
                ),
            )
        self.completed = True
        self.reporter.start_timing("report")

        try:
            report_agent = (
                build_report_agent(build_non_thinking_model(model))
                if model is not None
                else None
            )
            result = create_run_report(
                run_dir=self.run_dir,
                state=state,
                model_name=self.model_name,
                worktree_status=worktree_status,
                report_agent=report_agent,
            )
        except Exception as exc:
            failure = make_failure("INTERNAL", f"Report 收尾失败：{exc}")
            try:
                fallback = create_run_report(
                    run_dir=self.run_dir,
                    state=state,
                    model_name=self.model_name,
                    worktree_status=worktree_status,
                    report_agent=None,
                )
            except Exception as fallback_exc:
                result = ReportResult(
                    path=None,
                    fallback_used=True,
                    failure=make_failure(
                        "INTERNAL",
                        f"{failure.message}；程序模板生成失败：{fallback_exc}",
                    ),
                )
            else:
                if fallback.failure:
                    failure = make_failure(
                        "INTERNAL",
                        f"{failure.message}；程序模板保存失败："
                        f"{fallback.failure.message}",
                    )
                result = ReportResult(
                    path=fallback.path,
                    fallback_used=True,
                    failure=failure,
                )

        self.reporter.report_completed(result)
        return result
