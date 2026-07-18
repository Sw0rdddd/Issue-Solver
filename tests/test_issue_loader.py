import json
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from services import issue_loader
from services.issue_loader import RawIssue


class FakeResponse:
    def __init__(self, data: bytes):
        self.data = data

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.data


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/openai/example/issues/12",
        "https://github.com/openai/example/issues/12/",
        "  https://github.com/openai/example/issues/12  ",
    ],
)
def test_is_github_issue_url_accepts_standard_urls(url: str) -> None:
    assert issue_loader.is_github_issue_url(url) is True


@pytest.mark.parametrize(
    "value",
    [
        "https://github.com/openai/example/pull/12",
        "https://github.com/openai/example/issues/not-a-number",
        "https://example.com/openai/example/issues/12",
        "普通 Issue 文本",
    ],
)
def test_is_github_issue_url_rejects_other_inputs(value: str) -> None:
    assert issue_loader.is_github_issue_url(value) is False


def test_load_text_issue_strips_whitespace() -> None:
    issue = issue_loader.load_text_issue("  查询失败  ")

    assert issue == RawIssue(title="", body="查询失败", source="text")


@pytest.mark.parametrize("value", ["", "   ", "\n\t"])
def test_load_text_issue_rejects_empty_input(value: str) -> None:
    with pytest.raises(ValueError, match="Issue 内容不能为空"):
        issue_loader.load_text_issue(value)


@pytest.mark.parametrize("suffix", [".md", ".TXT"])
def test_load_local_issue_reads_utf8_text(
    tmp_path: Path,
    suffix: str,
) -> None:
    issue_path = tmp_path / f"ISSUE{suffix}"
    issue_path.write_text("  # 查询失败\n\n空结果返回 500。  ", encoding="utf-8")

    issue = issue_loader.load_local_issue(str(issue_path.resolve()))

    assert issue == RawIssue(
        title="",
        body="# 查询失败\n\n空结果返回 500。",
        source=str(issue_path.resolve()),
    )


def test_load_local_issue_requires_absolute_path() -> None:
    with pytest.raises(ValueError, match="必须使用绝对路径"):
        issue_loader.load_local_issue("workspace_test/ISSUE.md")


def test_load_local_issue_rejects_unsupported_extension(tmp_path: Path) -> None:
    issue_path = (tmp_path / "ISSUE.json").resolve()

    with pytest.raises(ValueError, match="仅支持 .md 或 .txt"):
        issue_loader.load_local_issue(str(issue_path))


def test_load_local_issue_rejects_missing_file(tmp_path: Path) -> None:
    issue_path = (tmp_path / "missing.md").resolve()

    with pytest.raises(ValueError, match="不存在或不是文件"):
        issue_loader.load_local_issue(str(issue_path))


def test_load_local_issue_rejects_directory(tmp_path: Path) -> None:
    issue_path = tmp_path / "directory.md"
    issue_path.mkdir()

    with pytest.raises(ValueError, match="不存在或不是文件"):
        issue_loader.load_local_issue(str(issue_path.resolve()))


def test_load_local_issue_rejects_empty_file(tmp_path: Path) -> None:
    issue_path = tmp_path / "ISSUE.md"
    issue_path.write_text(" \n\t ", encoding="utf-8")

    with pytest.raises(ValueError, match="文件内容不能为空"):
        issue_loader.load_local_issue(str(issue_path.resolve()))


def test_load_local_issue_rejects_non_utf8_file(tmp_path: Path) -> None:
    issue_path = tmp_path / "ISSUE.txt"
    issue_path.write_bytes(b"\xff\xfe")

    with pytest.raises(ValueError, match="必须使用 UTF-8 编码"):
        issue_loader.load_local_issue(str(issue_path.resolve()))


def test_load_local_issue_maps_read_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    issue_path = tmp_path / "ISSUE.md"
    issue_path.write_text("正文", encoding="utf-8")

    def fail_read_text(path: Path, *, encoding: str) -> str:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    with pytest.raises(RuntimeError, match="无法读取本地 Issue 文件"):
        issue_loader.load_local_issue(str(issue_path.resolve()))


def test_load_issue_routes_github_url(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://github.com/openai/example/issues/12"
    expected = RawIssue(title="标题", body="正文", source=url)
    received: list[str] = []

    def fake_load_github_issue(value: str) -> RawIssue:
        received.append(value)
        return expected

    monkeypatch.setattr(issue_loader, "load_github_issue", fake_load_github_issue)

    assert issue_loader.load_issue(url) == expected
    assert received == [url]


def test_load_issue_routes_plain_text() -> None:
    assert issue_loader.load_issue("  修复查询失败  ") == RawIssue(
        title="",
        body="修复查询失败",
        source="text",
    )


def test_load_issue_routes_absolute_local_file(tmp_path: Path) -> None:
    issue_path = tmp_path / "ISSUE.md"
    issue_path.write_text("# 本地 Issue", encoding="utf-8")

    assert issue_loader.load_issue(str(issue_path.resolve())) == RawIssue(
        title="",
        body="# 本地 Issue",
        source=str(issue_path.resolve()),
    )


def test_load_issue_rejects_relative_local_file() -> None:
    with pytest.raises(ValueError, match="必须使用绝对路径"):
        issue_loader.load_issue("workspace_test/ISSUE.md")


def test_load_issue_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="Issue 输入不能为空"):
        issue_loader.load_issue("   ")


def test_load_github_issue_reads_public_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    payload = {"title": "修复查询", "body": "空结果时返回 500"}

    def fake_urlopen(request: object, timeout: int) -> FakeResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    monkeypatch.setattr(issue_loader, "urlopen", fake_urlopen)

    url = "https://github.com/openai/example/issues/12"
    issue = issue_loader.load_github_issue(url)
    request = captured["request"]

    assert issue == RawIssue(
        title="修复查询",
        body="空结果时返回 500",
        source=url,
    )
    assert captured["timeout"] == 20
    assert request.full_url == "https://api.github.com/repos/openai/example/issues/12"
    assert request.get_header("Authorization") == "Bearer secret-token"
    assert request.get_header("User-agent") == "issue-solver"


def test_load_github_issue_rejects_invalid_url() -> None:
    with pytest.raises(ValueError, match="无效的 GitHub Issue URL"):
        issue_loader.load_github_issue("https://github.com/openai/example/pull/12")


@pytest.mark.parametrize(
    ("status_code", "exception", "message"),
    [
        (404, ValueError, "不存在"),
        (403, RuntimeError, "请求被拒绝"),
        (500, RuntimeError, "状态码：500"),
    ],
)
def test_load_github_issue_maps_http_errors(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    exception: type[Exception],
    message: str,
) -> None:
    def fake_urlopen(request: object, timeout: int) -> FakeResponse:
        raise HTTPError(request.full_url, status_code, "error", None, None)

    monkeypatch.setattr(issue_loader, "urlopen", fake_urlopen)

    with pytest.raises(exception, match=message):
        issue_loader.load_github_issue(
            "https://github.com/openai/example/issues/12"
        )


def test_load_github_issue_maps_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request: object, timeout: int) -> FakeResponse:
        raise URLError("offline")

    monkeypatch.setattr(issue_loader, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="无法连接 GitHub：offline"):
        issue_loader.load_github_issue(
            "https://github.com/openai/example/issues/12"
        )


def test_load_github_issue_rejects_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        issue_loader,
        "urlopen",
        lambda request, timeout: FakeResponse(b"not-json"),
    )

    with pytest.raises(RuntimeError, match="不是有效 JSON"):
        issue_loader.load_github_issue(
            "https://github.com/openai/example/issues/12"
        )


def test_load_github_issue_rejects_pull_request_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"title": "PR", "body": "", "pull_request": {}}
    monkeypatch.setattr(
        issue_loader,
        "urlopen",
        lambda request, timeout: FakeResponse(json.dumps(payload).encode("utf-8")),
    )

    with pytest.raises(ValueError, match="Pull Request"):
        issue_loader.load_github_issue(
            "https://github.com/openai/example/issues/12"
        )
