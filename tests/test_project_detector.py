from pathlib import Path

import pytest

from services.project_detector import detect_project_type, detect_test_commands


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        ("pyproject.toml", "python"),
        ("requirements.txt", "python"),
        ("setup.py", "python"),
        ("package.json", "node"),
        ("go.mod", "go"),
        ("Cargo.toml", "rust"),
        ("pom.xml", "java"),
        ("build.gradle", "java"),
        ("build.gradle.kts", "java"),
    ],
)
def test_detect_project_type_from_marker(
    tmp_path: Path,
    marker: str,
    expected: str,
) -> None:
    (tmp_path / marker).write_text("", encoding="utf-8")

    assert detect_project_type(tmp_path) == expected


def test_detect_project_type_returns_unknown_without_markers(
    tmp_path: Path,
) -> None:
    assert detect_project_type(tmp_path) == "unknown"


def test_python_marker_has_priority_over_node_marker(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")

    assert detect_project_type(tmp_path) == "python"


def test_pytest_ini_has_priority_over_tox(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "tox.ini").write_text("[tox]\n", encoding="utf-8")

    assert detect_test_commands(tmp_path) == ["pytest -q"]


def test_rejects_tox_without_pytest_configuration_or_tests(tmp_path: Path) -> None:
    (tmp_path / "tox.ini").write_text("[tox]\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="不会调用 tox"):
        detect_test_commands(tmp_path)


def test_detects_pytest_configuration_in_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\naddopts = '-q'\n",
        encoding="utf-8",
    )

    assert detect_test_commands(tmp_path) == ["pytest -q"]


def test_detects_tests_directory(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()

    assert detect_test_commands(tmp_path) == ["pytest -q"]


def test_tests_directory_has_priority_over_tox(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tox.ini").write_text("[tox]\n", encoding="utf-8")

    assert detect_test_commands(tmp_path) == ["pytest -q"]


def test_detects_pytest_configuration_in_tox_ini(tmp_path: Path) -> None:
    (tmp_path / "testing").mkdir()
    (tmp_path / "tox.ini").write_text(
        "[tox]\n\n[pytest]\ntestpaths = testing\n",
        encoding="utf-8",
    )

    assert detect_test_commands(tmp_path) == ["pytest -q"]


def test_returns_no_test_commands_without_markers(tmp_path: Path) -> None:
    assert detect_test_commands(tmp_path) == []
