from pathlib import Path

from tools.filesystem import list_files, read_file
from tools.search import search_symbol, search_text


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
