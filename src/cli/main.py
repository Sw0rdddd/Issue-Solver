import time
from collections.abc import Sequence

from cli.arguments import build_parser
from cli.run import run_command
from cli.terminal import TerminalReporter
from schemas.failure import failure_from_exception
from services.token_usage import TokenUsageMonitor


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
        token_usage = TokenUsageMonitor()
        started = time.monotonic()
        try:
            return run_command(
                args,
                reporter=reporter,
                token_usage=token_usage,
                global_mode=global_mode,
            )
        except Exception as exc:
            failure = failure_from_exception(exc, "INTERNAL")
            reporter.error_block(
                "运行失败",
                reporter.failure_details(failure),
            )
            reporter.set_outcome(
                success=False,
                result={"failure": failure},
            )
            return 1
        finally:
            reporter.summary(
                token_usage=token_usage.summary(),
                total_duration=time.monotonic() - started,
            )

    parser.error(f"未知命令：{args.command}")


def global_main() -> int:
    """供安装后的 issue-solver 控制台命令调用。"""

    return main(global_mode=True)


if __name__ == "__main__":
    raise SystemExit(main())
