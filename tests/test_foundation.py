import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from config import PROJECT_ROOT, Setting
from services.run_store import create_run_id


RUN_ID_PATTERN = re.compile(r"^run_[0-9A-HJKMNP-TV-Z]{26}$")


def test_project_root_matches_repository_root() -> None:
    assert PROJECT_ROOT == Path(__file__).parents[1]


def test_setting_reflects_loaded_environment() -> None:
    setting = Setting()

    assert setting.API_KEY == os.environ.get("API_KEY")
    assert setting.BASE_URL == os.environ.get("BASE_URL")
    assert setting.MODEL_NAME == os.environ.get("MODEL_NAME")
    assert setting.MAX_CYCLES == int(os.environ.get("MAX_CYCLES", "5"))
    assert setting.TEST_TIMEOUT == float(os.environ.get("TEST_TIMEOUT", "300"))
    assert setting.TEST_TAIL_LINES == int(
        os.environ.get("TEST_TAIL_LINES", "100")
    )
    assert setting.RUN_ROOT == Path(
        os.environ.get("RUN_ROOT", ".issue-solver-runs")
    ).expanduser()


def test_setting_reads_runtime_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_CYCLES", "7")
    monkeypatch.setenv("TEST_TIMEOUT", "12.5")
    monkeypatch.setenv("TEST_TAIL_LINES", "42")
    monkeypatch.setenv("RUN_ROOT", "custom-runs")

    setting = Setting()

    assert setting.MAX_CYCLES == 7
    assert setting.TEST_TIMEOUT == 12.5
    assert setting.TEST_TAIL_LINES == 42
    assert setting.RUN_ROOT == Path("custom-runs")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MAX_CYCLES", "0"),
        ("TEST_TIMEOUT", "not-a-number"),
        ("TEST_TAIL_LINES", "-1"),
        ("RUN_ROOT", "   "),
    ],
)
def test_setting_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError):
        Setting()


def test_dotenv_overrides_process_environment() -> None:
    expected_max_cycles = str(Setting().MAX_CYCLES)
    environment = os.environ.copy()
    environment["MAX_CYCLES"] = "99"

    result = subprocess.run(
        [sys.executable, "-c", "from config import Setting; print(Setting().MAX_CYCLES)"],
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == expected_max_cycles


def test_dotenv_is_loaded_from_project_root_not_current_directory(
    tmp_path: Path,
) -> None:
    expected_max_cycles = str(Setting().MAX_CYCLES)
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    (target_repo / ".env").write_text("MAX_CYCLES=99\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-c", "from config import Setting; print(Setting().MAX_CYCLES)"],
        cwd=target_repo,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == expected_max_cycles


def test_project_declares_global_issue_solver_command() -> None:
    pyproject = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert pyproject["project"]["name"] == "issue-solver"
    assert pyproject["project"]["scripts"]["issue-solver"] == "cli.main:global_main"
    assert pyproject["build-system"]["build-backend"] == "setuptools.build_meta"
    assert pyproject["tool"]["setuptools"]["package-dir"] == {"": "src"}


def test_create_run_id_returns_unique_ulids() -> None:
    first = create_run_id()
    second = create_run_id()

    assert RUN_ID_PATTERN.fullmatch(first)
    assert RUN_ID_PATTERN.fullmatch(second)
    assert first != second
