from pathlib import Path

import pytest

from tools import search as search_tools


def test_search_limits_use_expected_defaults() -> None:
    assert search_tools.MAX_SEARCH_FILE_BYTES == 1_048_576
    assert search_tools.MAX_SEARCH_FILES == 2_500
    assert search_tools.MAX_SYMBOL_RESULTS == 150


def test_searchable_file_rejects_non_regular_file(tmp_path: Path) -> None:
    directory = tmp_path / "directory.py"
    directory.mkdir()

    assert search_tools._read_searchable_file(tmp_path, directory) is None


def test_search_tools_skip_external_symlink(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text(
        "def leaked_symbol():\n    return 'leaked text'\n",
        encoding="utf-8",
    )
    link = tmp_path / "linked.py"

    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("当前环境不允许创建符号链接")

    text_result = search_tools.search_text.invoke(
        {
            "repo_path": str(tmp_path),
            "query": "leaked text",
        }
    )
    symbol_result = search_tools.search_symbol.invoke(
        {
            "repo_path": str(tmp_path),
            "symbol": "leaked_symbol",
        }
    )

    assert "未找到文本" in text_result
    assert "未找到符号" in symbol_result


@pytest.mark.parametrize(
    ("file_name", "content_kind", "query", "symbol"),
    [
        (
            "large.py",
            "large",
            "large text",
            "large_symbol",
        ),
        (
            "binary.py",
            "binary",
            "binary text",
            "binary_symbol",
        ),
        (
            "invalid.py",
            "invalid",
            "invalid text",
            "invalid_symbol",
        ),
    ],
)
def test_search_tools_skip_unsafe_content(
    tmp_path: Path,
    file_name: str,
    content_kind: str,
    query: str,
    symbol: str,
) -> None:
    if content_kind == "large":
        content = (
            b"def large_symbol():\n    return 'large text'\n#"
            + b"x" * 1_048_576
        )
    elif content_kind == "binary":
        content = b"def binary_symbol():\n    return 'binary text'\n\x00"
    else:
        content = b"def invalid_symbol():\n    return 'invalid text'\n\xff"

    (tmp_path / file_name).write_bytes(content)

    text_result = search_tools.search_text.invoke(
        {
            "repo_path": str(tmp_path),
            "query": query,
        }
    )
    symbol_result = search_tools.search_symbol.invoke(
        {
            "repo_path": str(tmp_path),
            "symbol": symbol,
        }
    )

    assert "未找到文本" in text_result
    assert "未找到符号" in symbol_result


def test_search_symbol_finds_async_function(tmp_path: Path) -> None:
    (tmp_path / "worker.py").write_text(
        "async def fetch_result():\n    return 1\n",
        encoding="utf-8",
    )

    result = search_tools.search_symbol.invoke(
        {
            "repo_path": str(tmp_path),
            "symbol": "fetch_result",
        }
    )

    assert result == "function fetch_result -> worker.py:1"


def test_search_text_reports_result_truncation(tmp_path: Path) -> None:
    (tmp_path / "matches.txt").write_text(
        "needle one\nneedle two\nneedle three\n",
        encoding="utf-8",
    )

    result = search_tools.search_text.invoke(
        {
            "repo_path": str(tmp_path),
            "query": "needle",
            "max_results": 2,
        }
    )

    assert result.splitlines()[-1] == (
        "[结果已截断，当前显示前 2 条，请缩小 path 或 file_pattern]"
    )
    assert len(result.splitlines()) == 3


def test_search_symbol_reports_result_truncation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(search_tools, "MAX_SYMBOL_RESULTS", 2)
    (tmp_path / "symbols.py").write_text(
        "class repeated:\n    pass\n\n"
        "def repeated():\n    pass\n\n"
        "async def repeated():\n    pass\n",
        encoding="utf-8",
    )

    result = search_tools.search_symbol.invoke(
        {
            "repo_path": str(tmp_path),
            "symbol": "repeated",
        }
    )

    assert result.splitlines()[-1] == "[结果已截断，当前显示前 2 条]"
    assert len(result.splitlines()) == 3


def test_search_tools_do_not_report_truncation_at_exact_result_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(search_tools, "MAX_SYMBOL_RESULTS", 2)
    (tmp_path / "exact.py").write_text(
        "def repeated():\n    pass\n\n"
        "async def repeated():\n    pass\n\n"
        "# needle one\n"
        "# needle two\n",
        encoding="utf-8",
    )

    text_result = search_tools.search_text.invoke(
        {
            "repo_path": str(tmp_path),
            "query": "needle",
            "max_results": 2,
        }
    )
    symbol_result = search_tools.search_symbol.invoke(
        {
            "repo_path": str(tmp_path),
            "symbol": "repeated",
        }
    )

    assert "截断" not in text_result
    assert "截断" not in symbol_result


def test_search_tools_report_file_scan_truncation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(search_tools, "MAX_SEARCH_FILES", 2)
    for index in range(3):
        (tmp_path / f"module_{index}.py").write_text(
            f"value_{index} = {index}\n",
            encoding="utf-8",
        )

    text_result = search_tools.search_text.invoke(
        {
            "repo_path": str(tmp_path),
            "query": "missing",
        }
    )
    symbol_result = search_tools.search_symbol.invoke(
        {
            "repo_path": str(tmp_path),
            "symbol": "missing",
        }
    )

    assert text_result == (
        "[搜索已截断，当前仅扫描前 2 个候选文件，"
        "请缩小 path 或 file_pattern]"
    )
    assert symbol_result == (
        "[搜索已截断，当前仅扫描前 2 个候选文件，请缩小 path]"
    )
