from pathlib import Path
from typing import Literal


ProjectType = Literal[
    "python",
    "node",
    "go",
    "rust",
    "java",
    "unknown",
]


def detect_project_type(repo_path: Path) -> ProjectType:
    """根据仓库根目录中的配置文件识别主要项目类型。"""

    markers: list[tuple[str, ProjectType]] = [
        ("pyproject.toml", "python"),
        ("requirements.txt", "python"),
        ("setup.py", "python"),
        ("package.json", "node"),
        ("go.mod", "go"),
        ("Cargo.toml", "rust"),
        ("pom.xml", "java"),
        ("build.gradle", "java"),
        ("build.gradle.kts", "java"),
    ]

    for filename, project_type in markers:
        if (repo_path / filename).exists():
            return project_type

    return "unknown"



def detect_test_commands(repo_path: Path) -> list[str]:
    """根据 Python 项目配置识别基础测试命令。"""

    if (repo_path / "pytest.ini").exists():
        return ["pytest -q"]

    pyproject_path = repo_path / "pyproject.toml"

    if pyproject_path.exists():
        content = pyproject_path.read_text(
            encoding="utf-8",
            errors="ignore",
        )

        if "[tool.pytest.ini_options]" in content:
            return ["pytest -q"]

    if (repo_path / "tests").is_dir():
        return ["pytest -q"]

    if (repo_path / "tox.ini").exists():
        raise RuntimeError(
            "首版只允许 pytest；检测到 tox.ini，但未检测到 pytest 配置或 tests 目录。"
        )

    return []
