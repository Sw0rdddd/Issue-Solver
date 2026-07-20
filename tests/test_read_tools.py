from pathlib import Path

from tools.filesystem import list_files, read_file
from tools.search import search_symbol, search_text


def test_list_files_limits_entries_and_reports_truncation(
    tmp_path: Path,
) -> None:
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text(name, encoding="utf-8")

    result = list_files.invoke(
        {
            "repo_path": str(tmp_path),
            "max_entries": 2,
        }
    )

    assert result == (
        "[FILE] a.py\n"
        "[FILE] b.py\n"
        "[目录结果已截断，共显示前 2 项，请缩小 path 或 max_depth]"
    )

    complete_result = list_files.invoke(
        {
            "repo_path": str(tmp_path),
            "max_entries": 3,
        }
    )

    assert "截断" not in complete_result
    assert len(complete_result.splitlines()) == 3


def test_list_files_validates_entry_limit(tmp_path: Path) -> None:
    assert "max_entries 必须大于等于 1" in list_files.invoke(
        {
            "repo_path": str(tmp_path),
            "max_entries": 0,
        }
    )
    assert "max_entries 不能大于 2000" in list_files.invoke(
        {
            "repo_path": str(tmp_path),
            "max_entries": 2_001,
        }
    )


def test_read_file_includes_path_and_line_range(tmp_path: Path) -> None:
    target = tmp_path / "nodes" / "coordinator.py"
    target.parent.mkdir()
    target.write_text(
        "\n".join(f"line {line_number}" for line_number in range(1, 221)),
        encoding="utf-8",
    )

    result = read_file.invoke(
        {
            "repo_path": str(tmp_path),
            "path": "nodes/coordinator.py",
            "start_line": 1,
            "end_line": 200,
        }
    )

    assert result.startswith(
        "File: nodes/coordinator.py\n"
        "Lines: 1-200 of 220\n\n"
        "   1 | line 1"
    )
    assert result.endswith(" 200 | line 200")


def test_read_file_reports_actual_end_line(tmp_path: Path) -> None:
    target = tmp_path / "short.py"
    target.write_text("first\nsecond\nthird\n", encoding="utf-8")

    result = read_file.invoke(
        {
            "repo_path": str(tmp_path),
            "path": "./short.py",
            "start_line": 2,
            "end_line": 200,
        }
    )

    assert result == (
        "File: short.py\n"
        "Lines: 2-3 of 3\n\n"
        "   2 | second\n"
        "   3 | third"
    )


def test_read_only_tools_reject_explicit_environment_paths(tmp_path: Path) -> None:
    environment = tmp_path / ".conda"
    environment.mkdir()
    (environment / "secret.py").write_text(
        "def hidden_symbol():\n    return 'hidden'\n",
        encoding="utf-8",
    )

    assert "禁止访问" in list_files.invoke(
        {"repo_path": str(tmp_path), "path": ".conda"}
    )
    assert "禁止读取" in read_file.invoke(
        {"repo_path": str(tmp_path), "path": ".conda/secret.py"}
    )
    assert "禁止搜索" in search_text.invoke(
        {"repo_path": str(tmp_path), "path": ".conda", "query": "hidden"}
    )
    assert "禁止搜索" in search_symbol.invoke(
        {
            "repo_path": str(tmp_path),
            "path": ".conda",
            "symbol": "hidden_symbol",
        }
    )
