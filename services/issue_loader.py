import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from config import Setting


GITHUB_ISSUE_PATTERN = re.compile(
    r"^https://github\.com/"
    r"(?P<owner>[^/]+)/"
    r"(?P<repo>[^/]+)/"
    r"issues/"
    r"(?P<number>\d+)/?$"
)
LOCAL_ISSUE_SUFFIXES = {".md", ".txt"}


@dataclass
class RawIssue:
    """尚未经过 LLM 规范化的原始 Issue。"""

    title: str
    body: str
    source: str


def is_github_issue_url(value: str) -> bool:
    """判断输入是否为标准 GitHub Issue URL。"""

    return GITHUB_ISSUE_PATTERN.fullmatch(value.strip()) is not None


def load_text_issue(issue_text: str) -> RawIssue:
    """加载用户直接输入的 Issue 文本。"""

    text = issue_text.strip()

    if not text:
        raise ValueError("Issue 内容不能为空。")

    return RawIssue(
        title="",
        body=text,
        source="text",
    )


def load_local_issue(issue_path: str) -> RawIssue:
    """从本地 UTF-8 文本文件加载 Issue。"""

    path = Path(issue_path.strip())

    if not path.is_absolute():
        raise ValueError(f"本地 Issue 文件必须使用绝对路径：{path}")

    if path.suffix.lower() not in LOCAL_ISSUE_SUFFIXES:
        raise ValueError(f"本地 Issue 文件仅支持 .md 或 .txt：{path}")

    if not path.is_file():
        raise ValueError(f"本地 Issue 文件不存在或不是文件：{path}")

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"本地 Issue 文件必须使用 UTF-8 编码：{path}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"无法读取本地 Issue 文件：{path}") from exc

    body = content.strip()
    if not body:
        raise ValueError(f"本地 Issue 文件内容不能为空：{path}")

    return RawIssue(
        title="",
        body=body,
        source=str(path.resolve()),
    )


def load_github_issue(issue_url: str) -> RawIssue:
    """通过 GitHub API 加载公开 Issue。"""

    url = issue_url.strip()
    match = GITHUB_ISSUE_PATTERN.fullmatch(url)

    if match is None:
        raise ValueError(f"无效的 GitHub Issue URL：{url}")

    owner = match.group("owner")
    repo = match.group("repo")
    issue_number = match.group("number")

    api_url = (
        f"https://api.github.com/repos/"
        f"{owner}/{repo}/issues/{issue_number}"
    )

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "issue-solver",
    }

    github_token = Setting().GITHUB_TOKEN
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    request = Request(
        api_url,
        headers=headers,
        method="GET",
    )

    try:
        with urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))

    except HTTPError as exc:
        if exc.code == 404:
            raise ValueError(
                "GitHub Issue 不存在，或者当前没有访问权限。"
            ) from exc

        if exc.code == 403:
            raise RuntimeError(
                "GitHub API 请求被拒绝，可能是请求频率受限。"
            ) from exc

        raise RuntimeError(
            f"GitHub API 请求失败，状态码：{exc.code}"
        ) from exc

    except URLError as exc:
        raise RuntimeError(
            f"无法连接 GitHub：{exc.reason}"
        ) from exc

    except json.JSONDecodeError as exc:
        raise RuntimeError("GitHub 返回的数据不是有效 JSON。") from exc

    # GitHub 的 Issue API 也可能返回 Pull Request。
    if "pull_request" in data:
        raise ValueError("该 URL 对应的是 Pull Request，不是 Issue。")

    return RawIssue(
        title=data.get("title") or "",
        body=data.get("body") or "",
        source=url,
    )


def load_issue(issue_input: str) -> RawIssue:
    """根据用户输入类型加载 Issue。"""

    value = issue_input.strip()

    if not value:
        raise ValueError("Issue 输入不能为空。")

    if is_github_issue_url(value):
        return load_github_issue(value)

    path = Path(value)
    if path.is_absolute():
        return load_local_issue(value)

    if path.suffix.lower() in LOCAL_ISSUE_SUFFIXES:
        raise ValueError(f"本地 Issue 文件必须使用绝对路径：{path}")

    return load_text_issue(value)
