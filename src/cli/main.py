import time
from collections.abc import Sequence

from langchain_core.callbacks import (
    UsageMetadataCallbackHandler,
    get_usage_metadata_callback,
)

from cli.arguments import build_parser
from cli.run import run_command
from cli.terminal import TerminalReporter
from schemas.failure import failure_from_exception


def _total_tokens(callback: UsageMetadataCallbackHandler) -> int:
    """汇总本次运行中所有模型调用上报的 Token。"""

    return sum(
        usage.get("total_tokens", 0)
        for usage in callback.usage_metadata.values()
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    global_mode: bool = False,
) -> int:
    """解析参数、运行命令并返回适合命令行使用的退出码。"""

    parser = build_parser(global_mode=global_mode)
    args = parser.parse_args(argv)

    if args.command == "run":
        reporter = TerminalReporter(
            quiet=args.quiet,
            leading_blank=True,
        )
        started = time.monotonic()
        with get_usage_metadata_callback() as usage_callback:
            try:
                return run_command(
                    args,
                    reporter=reporter,
                    global_mode=global_mode,
                )
            except Exception as exc:
                failure = failure_from_exception(exc, "INTERNAL")
                reporter.error_block(
                    "运行失败",
                    reporter.failure_details(failure),
                )
                reporter.set_outcome(success=False)
                return 1
            finally:
                reporter.summary(
                    total_tokens=_total_tokens(usage_callback),
                    total_duration=time.monotonic() - started,
                )

    parser.error(f"未知命令：{args.command}")


def global_main() -> int:
    """供安装后的 issue-solver 控制台命令调用。"""

    return main(global_mode=True)


if __name__ == "__main__":
    raise SystemExit(main())
