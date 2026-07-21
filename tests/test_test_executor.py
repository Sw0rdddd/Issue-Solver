import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from config import PROJECT_ROOT
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
from services.test_runtime import (
    TEST_RUNTIME_ROOT,
    TestRuntime as RuntimeHandle,
    finish_test_runtime as finish_runtime,
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
    basetemp_argument = next(
        argument
        for argument in result.resolved_command
        if argument.startswith("--basetemp=")
    )
    assert not Path(basetemp_argument.split("=", 1)[1]).parent.exists()


def test_execute_test_command_isolates_nested_pytest_from_run_root_config(
    tmp_path: Path,
) -> None:
    controller = tmp_path / "controller"
    controller.mkdir()
    (controller / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\ntestpaths = ['tests']\n",
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_nested.py").write_text(
        """pytest_plugins = ["pytester"]


def test_nested_pytest_uses_local_root(pytester):
    pytester.makepyfile(test_inner="def test_pass(): assert True")
    result = pytester.runpytest_subprocess("--collect-only", "-q")
    result.stdout.fnmatch_lines(["test_inner.py::test_pass"])
""",
        encoding="utf-8",
    )
    run_dir = controller / ".benchmark-runs" / "run"

    result = execute_test_command(
        repo_path=repo,
        run_dir=run_dir,
        command=python_pytest_command("-q", "test_nested.py"),
        environment=current_environment(),
        timeout=30,
        tail_lines=40,
        repair_round=1,
        index=1,
    )

    assert result.status == "PASSED"
    basetemp_argument = next(
        argument
        for argument in result.resolved_command
        if argument.startswith("--basetemp=")
    )
    basetemp = Path(basetemp_argument.split("=", 1)[1])
    assert not basetemp.is_relative_to(repo.resolve())
    assert not basetemp.is_relative_to(run_dir.resolve())
    assert basetemp.is_relative_to(TEST_RUNTIME_ROOT.resolve())
    assert not basetemp.parent.exists()


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
    basetemp_argument = next(
        argument
        for argument in result.resolved_command
        if argument.startswith("--basetemp=")
    )
    assert not Path(basetemp_argument.split("=", 1)[1]).parent.exists()


def test_execute_test_command_cleans_runtime_on_keyboard_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_sample.py").write_text(
        "def test_value():\n    assert True\n",
        encoding="utf-8",
    )
    runtime_path = tmp_path / "runtime"
    runtime_path.mkdir()
    process_state = {"killed": False, "cleaned": False}

    class InterruptedProcess:
        returncode = -1

        def wait(self, timeout: float | None = None) -> int:
            if not process_state["killed"]:
                raise KeyboardInterrupt
            return self.returncode

        def kill(self) -> None:
            process_state["killed"] = True

        def poll(self) -> int | None:
            return self.returncode if process_state["killed"] else None

    monkeypatch.setattr(
        "services.test_executor.start_test_runtime",
        lambda **kwargs: SimpleNamespace(path=runtime_path),
    )
    monkeypatch.setattr(
        "services.test_executor.subprocess.Popen",
        lambda *args, **kwargs: InterruptedProcess(),
    )
    monkeypatch.setattr(
        "services.test_executor.finish_test_runtime",
        lambda runtime: process_state.update(cleaned=True),
    )

    with pytest.raises(KeyboardInterrupt):
        execute_test_command(
            repo_path=repo,
            run_dir=tmp_path / "run",
            command=python_pytest_command("-q", "test_sample.py"),
            environment=current_environment(),
            timeout=30,
            tail_lines=20,
            repair_round=1,
            index=1,
        )

    assert process_state == {"killed": True, "cleaned": True}


def test_execute_test_command_reports_runtime_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_sample.py").write_text(
        "def test_value():\n    assert True\n",
        encoding="utf-8",
    )

    def report_cleanup_failure(runtime: RuntimeHandle) -> str:
        finish_runtime(runtime)
        return "无法清理测试临时目录：simulated cleanup failure"

    monkeypatch.setattr(
        "services.test_executor.finish_test_runtime",
        report_cleanup_failure,
    )

    result = execute_test_command(
        repo_path=repo,
        run_dir=tmp_path / "run",
        command=python_pytest_command("-q", "test_sample.py"),
        environment=current_environment(),
        timeout=30,
        tail_lines=20,
        repair_round=1,
        index=1,
    )

    assert result.status == "ENVIRONMENT_ERROR"
    assert result.failure.type == "ENVIRONMENT"
    assert result.exit_code == -1
    assert "无法清理测试临时目录" in result.output_tail


def test_runtime_watchdog_cleans_after_parent_is_killed() -> None:
    script = """
import time
from services.test_runtime import start_test_runtime

runtime = start_test_runtime(run_name="killed-parent", repair_round=1, index=1)
print(runtime.path, flush=True)
time.sleep(60)
"""
    parent = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert parent.stdout is not None
    runtime_path = Path(parent.stdout.readline().strip())

    try:
        assert runtime_path.is_dir()
        parent.kill()
        parent.wait(timeout=10)
        deadline = time.monotonic() + 10
        while runtime_path.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not runtime_path.exists()
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=10)


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
