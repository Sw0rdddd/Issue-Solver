import json
import os
import subprocess
import venv
from pathlib import Path

import pytest

from schemas.environment_info import EnvironmentInfo
from services import python_environment
from services.python_environment import (
    build_environment_variables,
    discover_python_environment,
)


def commit_ignore(repo: Path, entry: str) -> None:
    (repo / ".gitignore").write_text(f"{entry}/\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Issue Solver Tests",
            "-c",
            "user.email=tests@example.com",
            "commit",
            "-m",
            "ignore environment",
        ],
        cwd=repo,
        capture_output=True,
        check=True,
    )


def create_venv(path: Path, *, system_packages: bool) -> None:
    venv.EnvBuilder(
        with_pip=False,
        system_site_packages=system_packages,
    ).create(path)
    if not system_packages:
        return

    python = path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    result = subprocess.run(
        [str(python), "-c", "import site; print(site.getsitepackages()[0])"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    pytest_file = pytest.__file__
    assert pytest_file is not None
    dependency_root = Path(pytest_file).resolve().parent.parent
    site_packages = Path(result.stdout.strip())
    (site_packages / "issue_solver_test_dependencies.pth").write_text(
        str(dependency_root),
        encoding="utf-8",
    )


def test_discovers_repo_venv_and_validates_pytest(git_repo: Path) -> None:
    commit_ignore(git_repo, ".venv")
    create_venv(git_repo / ".venv", system_packages=True)

    run_dir = git_repo.parent / "run"
    result = discover_python_environment(git_repo, run_dir)

    assert result.kind == "VENV"
    assert result.source == ".venv"
    assert Path(result.root_path) == (git_repo / ".venv").resolve()
    assert result.pytest_version.startswith("pytest")
    assert (run_dir / "logs" / "environment_runtime" / "tmp").is_dir()


def test_discovers_repo_conda_with_platform_python(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = git_repo / ".conda"
    (root / "conda-meta").mkdir(parents=True)
    python = root / ("python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True, exist_ok=True)
    python.touch()
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], **kwargs: object):
        calls.append(arguments)
        if arguments[:2] == ["git", "check-ignore"]:
            return subprocess.CompletedProcess(arguments, 0)
        if arguments[0] != str(python.resolve()):
            raise AssertionError(f"使用了错误的解释器：{arguments[0]}")
        if arguments[1] == "-c":
            payload = {
                "executable": str(python.resolve()),
                "prefix": str(root.resolve()),
            }
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout=json.dumps(payload),
                stderr="",
            )
        if arguments[1:] == ["-m", "pytest", "--version"]:
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout="pytest test",
                stderr="",
            )
        raise AssertionError(f"意外的命令：{arguments}")

    monkeypatch.setattr(python_environment.subprocess, "run", fake_run)

    result = discover_python_environment(git_repo, git_repo.parent / "run")

    assert result.kind == "CONDA"
    assert result.source == ".conda"
    assert result.python_executable == str(python.resolve())
    assert result.pytest_version == "pytest test"
    assert [call[1:] for call in calls if call[0] == str(python.resolve())] == [
        [
            "-c",
            "import json,sys; print(json.dumps({'executable':sys.executable,'prefix':sys.prefix}))",
        ],
        ["-m", "pytest", "--version"],
    ]


def test_missing_repo_environment_does_not_fallback_to_active_python(
    git_repo: Path,
) -> None:
    with pytest.raises(RuntimeError, match="未发现"):
        discover_python_environment(git_repo, git_repo.parent / "run")


def test_rejects_unignored_environment(git_repo: Path) -> None:
    create_venv(git_repo / ".venv", system_packages=True)

    with pytest.raises(RuntimeError, match="Git ignore"):
        discover_python_environment(git_repo, git_repo.parent / "run")


def test_rejects_multiple_environment_candidates(git_repo: Path) -> None:
    (git_repo / ".venv").mkdir()
    (git_repo / "venv").mkdir()

    with pytest.raises(RuntimeError, match="多个虚拟环境"):
        discover_python_environment(git_repo, git_repo.parent / "run")


def test_rejects_environment_without_pytest(git_repo: Path) -> None:
    commit_ignore(git_repo, ".venv")
    create_venv(git_repo / ".venv", system_packages=False)

    with pytest.raises(RuntimeError, match="未安装可用的 pytest"):
        discover_python_environment(git_repo, git_repo.parent / "run")


def test_build_environment_variables_uses_run_directory(
    git_repo: Path,
) -> None:
    commit_ignore(git_repo, ".venv")
    create_venv(git_repo / ".venv", system_packages=True)
    info = discover_python_environment(git_repo, git_repo.parent / "run")
    runtime = git_repo.parent / "runtime"

    values = build_environment_variables(info, runtime)

    assert values["VIRTUAL_ENV"] == info.root_path
    assert values["TEMP"] == str(runtime / "tmp")
    assert values["TMP"] == str(runtime / "tmp")
    assert values["TMPDIR"] == str(runtime / "tmp")
    first_path = Path(values["PATH"].split(os.pathsep)[0])
    expected = Path(info.root_path) / ("Scripts" if os.name == "nt" else "bin")
    assert first_path == expected


def test_build_environment_variables_uses_conda_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / ".conda"
    python = root / ("python.exe" if os.name == "nt" else "bin/python")
    info = EnvironmentInfo(
        kind="CONDA",
        root_path=str(root),
        python_executable=str(python),
        pytest_version="pytest test",
        source=".conda",
    )
    monkeypatch.setenv("VIRTUAL_ENV", "active-venv")

    values = build_environment_variables(info, tmp_path / "runtime")

    assert values["CONDA_PREFIX"] == str(root)
    assert "VIRTUAL_ENV" not in values
    path_entries = values["PATH"].split(os.pathsep)
    expected_first = root if os.name == "nt" else root / "bin"
    assert Path(path_entries[0]) == expected_first
