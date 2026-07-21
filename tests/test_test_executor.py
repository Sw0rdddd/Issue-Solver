import subprocess
import sys
from pathlib import Path

import pytest

from schemas.environment_info import EnvironmentInfo
from services.test_executor import (
    MAX_MODEL_OUTPUT_CHARS,
    build_output_tail,
    build_targeted_test_command,
    execute_test_command,
    parse_test_command,
    resolve_pytest_command,
    worktree_fingerprint,
)


def python_pytest_command(*arguments: str) -> str:
    executable = Path(sys.executable).as_posix()
    return " ".join([executable, "-m", "pytest", *arguments])


def current_environment() -> EnvironmentInfo:
    return EnvironmentInfo(
        kind="VENV",
        root_path=str(Path(sys.prefix).resolve()),
        python_executable=str(Path(sys.executable).resolve()),
        pytest_version="pytest test",
        source=".venv",
    )


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q",
        "python -m pytest -q",
    ],
)
def test_parse_test_command_accepts_supported_runners(command: str) -> None:
    assert parse_test_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q | tee result.log",
        "pytest -q && echo done",
        "python script.py",
        "curl https://example.com",
        "tox",
        "py -m tox",
    ],
)
def test_parse_test_command_rejects_unsafe_or_free_commands(command: str) -> None:
    with pytest.raises(ValueError):
        parse_test_command(command)


def test_build_targeted_test_command_uses_repo_relative_node_ids(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    command = build_targeted_test_command(
        repo,
        ["tests/test_app.py::TestQuery::test_empty"],
    )

    assert command == "pytest -q tests/test_app.py::TestQuery::test_empty"


def test_build_targeted_test_command_rejects_symlink_escape(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = repo / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("当前环境不允许创建符号链接")

    with pytest.raises(ValueError, match="超出目标仓库"):
        build_targeted_test_command(repo, ["linked/test_external.py"])


@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        ("def test_value():\n    assert 1 == 1\n", "PASSED"),
        ("def test_value():\n    assert 1 == 2\n", "FAILED"),
    ],
)
def test_execute_test_command_records_complete_logs(
    tmp_path: Path,
    body: str,
    expected_status: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_sample.py").write_text(body, encoding="utf-8")
    run_dir = tmp_path / "run"

    result = execute_test_command(
        repo_path=repo,
        run_dir=run_dir,
        command=python_pytest_command("-q", "test_sample.py"),
        environment=current_environment(),
        timeout=30,
        tail_lines=20,
        repair_round=1,
        index=1,
    )

    assert result.status == expected_status
    if expected_status == "PASSED":
        assert result.failure is None
    else:
        assert result.failure.type == "SOLUTION"
    assert result.resolved_command[:3] == [
        str(Path(sys.executable).resolve()),
        "-m",
        "pytest",
    ]
    assert result.cwd == str(repo.resolve())
    assert Path(result.stdout_path).is_file()
    assert Path(result.stderr_path).is_file()
    assert Path(result.stdout_path).parent == run_dir / "logs"
    assert Path(result.stderr_path).parent == run_dir / "logs"
    complete_stdout = Path(result.stdout_path).read_text(encoding="utf-8")
    assert complete_stdout
    assert expected_status.lower().removesuffix("ed") in complete_stdout.lower()
    assert len(result.output_tail) <= MAX_MODEL_OUTPUT_CHARS


def test_execute_test_command_times_out(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_slow.py").write_text(
        "import time\n\ndef test_slow():\n    time.sleep(5)\n",
        encoding="utf-8",
    )

    result = execute_test_command(
        repo_path=repo,
        run_dir=tmp_path / "run",
        command=python_pytest_command("-q", "test_slow.py"),
        environment=current_environment(),
        timeout=0.2,
        tail_lines=20,
        repair_round=1,
        index=1,
    )

    assert result.status == "TIMEOUT"
    assert result.failure.type == "LIMIT"
    assert result.exit_code == -1
    assert "进程已终止" in Path(result.stderr_path).read_text(encoding="utf-8")


def test_execute_test_command_maps_rejected_command_to_environment_error(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    result = execute_test_command(
        repo_path=repo,
        run_dir=tmp_path / "run",
        command="curl https://example.com",
        environment=current_environment(),
        timeout=30,
        tail_lines=20,
        repair_round=1,
        index=1,
    )

    assert result.status == "ENVIRONMENT_ERROR"
    assert result.failure.type == "ENVIRONMENT"
    assert result.exit_code == -1
    assert "仅允许" in result.output_tail


def test_missing_test_dependency_is_environment_error(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_missing.py").write_text(
        "import package_that_does_not_exist_12345\n",
        encoding="utf-8",
    )

    result = execute_test_command(
        repo_path=repo,
        run_dir=tmp_path / "run",
        command="pytest -q test_missing.py",
        environment=current_environment(),
        timeout=30,
        tail_lines=30,
        repair_round=1,
        index=1,
    )

    assert result.status == "ENVIRONMENT_ERROR"
    assert result.failure.type == "ENVIRONMENT"
    assert result.exit_code == -1
    assert "package_that_does_not_exist_12345" in result.output_tail


def test_resolve_pytest_command_binds_selected_interpreter(tmp_path: Path) -> None:
    selected = tmp_path / ".venv" / "Scripts" / "python.exe"

    assert resolve_pytest_command("pytest -q tests", selected) == [
        str(selected.resolve()),
        "-m",
        "pytest",
        "-q",
        "tests",
    ]


def test_build_output_tail_keeps_full_files_but_limits_model_lines(
    tmp_path: Path,
) -> None:
    stdout = tmp_path / "stdout.log"
    stderr = tmp_path / "stderr.log"
    stdout.write_text(
        "".join(f"stdout-{index}\n" for index in range(50)),
        encoding="utf-8",
    )
    stderr.write_text(
        "".join(f"stderr-{index}\n" for index in range(10)),
        encoding="utf-8",
    )

    tail = build_output_tail(stdout, stderr, 12)

    assert len(stdout.read_text(encoding="utf-8").splitlines()) == 50
    assert len(stderr.read_text(encoding="utf-8").splitlines()) == 10
    assert len(tail.splitlines()) == 12
    assert "stdout-0" not in tail
    assert "stderr-9" in tail


def test_worktree_fingerprint_detects_test_mutation(git_repo: Path) -> None:
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    before = worktree_fingerprint(git_repo, base_commit)

    (git_repo / "generated.txt").write_text("generated\n", encoding="utf-8")

    assert worktree_fingerprint(git_repo, base_commit) != before
