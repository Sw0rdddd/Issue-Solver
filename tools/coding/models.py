import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, TypedDict


DEFAULT_PROTECTED_NAMES = frozenset(
    {
        ".git",
        ".issue-solver-runs",
        ".venv",
        "venv",
        ".conda",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        "dist",
        "build",
    }
)


class CodingToolResult(TypedDict):
    """Coding 工具统一返回结构。"""

    success: bool
    summary: str
    data: dict[str, Any]
    error: str | None
    truncated: bool


def tool_success(
    summary: str,
    data: dict[str, Any] | None = None,
    *,
    truncated: bool = False,
) -> CodingToolResult:
    return {
        "success": True,
        "summary": summary,
        "data": data or {},
        "error": None,
        "truncated": truncated,
    }


def tool_failure(error: str) -> CodingToolResult:
    return {
        "success": False,
        "summary": "操作失败。",
        "data": {},
        "error": error,
        "truncated": False,
    }


def _normalize_scope(value: str) -> str:
    raw = value.strip().replace("\\", "/")
    is_directory = raw.endswith("/")
    raw = raw.rstrip("/")

    if not raw or raw == ".":
        raise ValueError("允许或保护路径不能为空或仓库根目录。")

    if raw.startswith("/") or (
        len(raw) >= 2 and raw[0].isalpha() and raw[1] == ":"
    ):
        raise ValueError(f"路径范围必须是仓库相对路径：{value}")

    path = PurePosixPath(raw)
    if ".." in path.parts:
        raise ValueError(f"路径范围不能包含 ..：{value}")

    normalized = path.as_posix()
    return normalized + "/" if is_directory else normalized


def _run_git(repo_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ValueError("未找到 Git。") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Git 命令执行超时。") from exc

    if result.returncode != 0:
        error = result.stderr.strip() or "未知 Git 错误"
        raise ValueError(error)
    return result.stdout.strip()


@dataclass(frozen=True)
class CodingToolContext:
    """绑定一次 Coding 运行不可由 Agent 更改的安全上下文。"""

    repo_root: Path
    base_commit: str
    run_dir: Path
    allowed_paths: tuple[str, ...]
    protected_paths: tuple[str, ...]
    repair_round: int
    stage_call: int

    @classmethod
    def create(
        cls,
        *,
        repo_root: str | Path,
        base_commit: str,
        run_dir: str | Path,
        allowed_paths: list[str] | tuple[str, ...],
        protected_paths: list[str] | tuple[str, ...] = (),
        repair_round: int = 1,
        stage_call: int = 1,
        allow_existing_changes: bool = False,
    ) -> "CodingToolContext":
        if repair_round < 1:
            raise ValueError("repair_round 必须大于 0。")
        if stage_call < 1:
            raise ValueError("stage_call 必须大于 0。")

        root = Path(repo_root).resolve()
        if not root.is_dir():
            raise ValueError(f"目标仓库不存在或不是目录：{root}")

        actual_root = Path(
            _run_git(root, "rev-parse", "--show-toplevel")
        ).resolve()
        if actual_root != root:
            raise ValueError(f"repo_root 必须是 Git 仓库根目录：{root}")

        commit = base_commit.strip()
        if not commit:
            raise ValueError("base_commit 不能为空。")
        current_head = _run_git(root, "rev-parse", "HEAD")
        if current_head != commit:
            raise ValueError("base_commit 必须等于当前 HEAD。")

        status = _run_git(
            root,
            "status",
            "--porcelain",
            "--untracked-files=all",
        )
        if status and not allow_existing_changes:
            raise ValueError("创建 Coding 工具时 Git 工作区必须干净。")

        resolved_run_dir = Path(run_dir).resolve()
        try:
            resolved_run_dir.relative_to(root)
        except ValueError:
            pass
        else:
            raise ValueError("运行目录不能位于目标仓库内。")

        normalized_allowed = tuple(
            dict.fromkeys(_normalize_scope(path) for path in allowed_paths)
        )
        if not normalized_allowed:
            raise ValueError("allowed_paths 不能为空。")

        normalized_protected = tuple(
            dict.fromkeys(_normalize_scope(path) for path in protected_paths)
        )
        resolved_run_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            repo_root=root,
            base_commit=commit,
            run_dir=resolved_run_dir,
            allowed_paths=normalized_allowed,
            protected_paths=normalized_protected,
            repair_round=repair_round,
            stage_call=stage_call,
        )
