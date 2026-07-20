import argparse


def positive_int(value: str) -> int:
    """将命令行参数解析为正整数。"""

    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须是整数。") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("必须大于 0。")
    return number


def positive_float(value: str) -> float:
    """将命令行参数解析为正数。"""

    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须是数字。") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("必须大于 0。")
    return number


def build_parser(*, global_mode: bool = False) -> argparse.ArgumentParser:
    """创建 issue-solver 命令行解析器。"""

    parser = argparse.ArgumentParser(
        prog="issue-solver" if global_mode else "python -m cli.main",
        description="运行 issue-solver 最小工作流。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="解析 Issue 并探索目标仓库。",
    )
    run_parser.add_argument(
        "--repo",
        required=not global_mode,
        help=(
            "目标 Git 仓库路径。"
            if not global_mode
            else "目标 Git 仓库路径；省略时自动使用当前目录所在仓库。"
        ),
    )
    run_parser.add_argument(
        "--issue",
        required=True,
        help="Issue 文本、GitHub Issue URL 或本地 .md/.txt 绝对路径。",
    )
    run_parser.add_argument("--model", help="覆盖环境变量中的模型名称。")
    run_parser.add_argument(
        "--max-cycles",
        type=positive_int,
        help="覆盖 .env 中的 MAX_CYCLES。",
    )
    run_parser.add_argument(
        "--test-timeout",
        type=positive_float,
        help="覆盖 .env 中的 TEST_TIMEOUT，单位为秒。",
    )
    run_parser.add_argument(
        "--test-tail-lines",
        type=positive_int,
        help="覆盖 .env 中的 TEST_TAIL_LINES。",
    )
    run_parser.add_argument(
        "--run-root",
        help="覆盖 .env 中的 RUN_ROOT（相对路径基于 issue-solver 项目根目录）。",
    )
    run_parser.add_argument(
        "--quiet",
        action="store_true",
        help="隐藏执行过程，只显示最终摘要。",
    )

    return parser
