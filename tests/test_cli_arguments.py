import subprocess
import sys
from pathlib import Path

import pytest

from cli import main as main_module


def test_module_help_is_available() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "cli.main", "--help"],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "run" in result.stdout


def test_run_help_is_available(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main_module.main(["run", "--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "--repo" in output
    assert "--quiet" in output
    assert "--test-timeout" in output
    assert "--test-tail-lines" in output
    assert "--run-root" in output
    assert ".md/.txt 绝对路径" in output


def test_global_run_help_allows_omitting_repo(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main_module.main(["run", "--help"], global_mode=True)

    assert exc_info.value.code == 0
    assert "当前目录所在仓库" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["run", "--issue", "修复查询失败"],
        ["run", "--repo", "."],
        [
            "run",
            "--repo",
            ".",
            "--issue",
            "修复查询失败",
            "--max-cycles",
            "0",
        ],
    ],
)
def test_run_rejects_invalid_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main_module.main(argv)

    assert exc_info.value.code == 2
