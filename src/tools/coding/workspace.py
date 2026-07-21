import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from langchain_core.tools import BaseTool, tool

from schemas.failure import (
    ClassifiedFailure,
    FailureInfo,
    failure_from_exception,
    make_failure,
)
from services.artifacts import ensure_run_logs_directory
from tools.coding.models import (
    DEFAULT_PROTECTED_NAMES,
    CodingToolContext,
    CodingToolResult,
    tool_failure,
    tool_success,
)


MAX_PATCH_CHARS = 100_000
MAX_CODING_PATCH_ATTEMPTS = 10
MAX_CHANGED_FILES = 20
MAX_FILE_BYTES = 1_048_576
MAX_DIFF_PREVIEW_CHARS = 20_000
PATCH_FENCE_MARKERS = frozenset({"```", "```diff", "```patch"})
HUNK_HEADER_PATTERN = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$"
)


@dataclass(frozen=True)
class _PathState:
    relative_path: str
    existed: bool
    content: bytes | None
    mode: int | None


@dataclass(frozen=True)
class _ChangeSnapshot:
    diff: str
    changed_files: list[str]
    changes: list[dict[str, Any]]


def _run_git_bytes(
    context: CodingToolContext,
    args: list[str],
    *,
    input_bytes: bytes | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=context.repo_root,
            input=input_bytes,
            capture_output=True,
            timeout=30,
            check=False,
            env=env,
        )
    except FileNotFoundError as exc:
        raise ClassifiedFailure(
            make_failure("ENVIRONMENT", "未找到 Git。")
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ClassifiedFailure(
            make_failure("LIMIT", "Git 命令执行超时。")
        ) from exc


def _git_error(result: subprocess.CompletedProcess[bytes]) -> str:
    return result.stderr.decode("utf-8", errors="replace").strip() or "未知 Git 错误"


def _normalized_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or (len(normalized) >= 2 and normalized[0].isalpha() and normalized[1] == ":")
        or ".." in path.parts
    ):
        raise ClassifiedFailure(
            make_failure("SAFETY", f"Patch 包含仓库外或无效路径：{value}")
        )
    return path.as_posix()


def _matches_scope(path: str, scope: str) -> bool:
    if scope.endswith("/"):
        directory = scope[:-1]
        return path == directory or path.startswith(scope)
    return path == scope


def _validate_scoped_path(context: CodingToolContext, value: str) -> str:
    relative = _normalized_relative_path(value)
    parts = PurePosixPath(relative).parts

    if any(part in DEFAULT_PROTECTED_NAMES for part in parts) or any(
        _matches_scope(relative, scope) for scope in context.protected_paths
    ):
        raise ClassifiedFailure(
            make_failure("SAFETY", f"Patch 触碰受保护路径：{relative}")
        )

    if not any(
        _matches_scope(relative, scope) for scope in context.allowed_paths
    ):
        raise ClassifiedFailure(
            make_failure("SAFETY", f"Patch 路径超出允许修改范围：{relative}")
        )

    current = context.repo_root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ClassifiedFailure(
                make_failure("SAFETY", f"禁止修改符号链接或其后代：{relative}")
            )

    target = (context.repo_root / Path(*parts)).resolve(strict=False)
    try:
        target.relative_to(context.repo_root)
    except ValueError as exc:
        raise ClassifiedFailure(
            make_failure("SAFETY", f"Patch 路径解析到仓库之外：{relative}")
        ) from exc

    return relative


def _parse_numstat(data: bytes) -> list[tuple[str, ...]]:
    records: list[tuple[str, ...]] = []
    position = 0

    while position < len(data):
        first_tab = data.find(b"\t", position)
        second_tab = data.find(b"\t", first_tab + 1)
        end = data.find(b"\0", second_tab + 1)
        if first_tab < 0 or second_tab < 0 or end < 0:
            raise ValueError("无法解析 Patch 文件统计。")

        additions = data[position:first_tab]
        deletions = data[first_tab + 1:second_tab]
        if additions == b"-" or deletions == b"-":
            raise ValueError("禁止应用二进制 Patch。")

        first_path = data[second_tab + 1:end]
        position = end + 1
        if first_path:
            records.append((first_path.decode("utf-8"),))
            continue

        old_end = data.find(b"\0", position)
        new_end = data.find(b"\0", old_end + 1)
        if old_end < 0 or new_end < 0:
            raise ValueError("无法解析 Patch 重命名路径。")
        old_path = data[position:old_end].decode("utf-8")
        new_path = data[old_end + 1:new_end].decode("utf-8")
        records.append((old_path, new_path))
        position = new_end + 1

    return records


def _extract_patch_paths(
    context: CodingToolContext,
    patch_bytes: bytes,
) -> list[str]:
    result = _run_git_bytes(
        context,
        ["apply", "--numstat", "-z", "--recount", "-"],
        input_bytes=patch_bytes,
    )
    if result.returncode != 0:
        raise ValueError(f"Patch 格式无效：{_git_error(result)}")

    parsed_paths = {
        path
        for record in _parse_numstat(result.stdout)
        for path in record
    }
    patch_text = patch_bytes.decode("utf-8")
    parsed_paths.update(
        match.group(1)
        for match in re.finditer(
            r"^rename (?:from|to) (.+)$",
            patch_text,
            re.MULTILINE,
        )
    )
    paths = sorted(
        _validate_scoped_path(context, path) for path in parsed_paths
    )
    if not paths:
        raise ValueError("Patch 未包含任何文件修改。")
    if len(paths) > MAX_CHANGED_FILES:
        raise ValueError(
            f"单次 Patch 最多触碰 {MAX_CHANGED_FILES} 个文件。"
        )
    return paths


def _validate_patch_headers(patch: str) -> None:
    if not patch.strip():
        raise ValueError("Patch 不能为空。")
    if len(patch) > MAX_PATCH_CHARS:
        raise ValueError(f"Patch 不能超过 {MAX_PATCH_CHARS} 个字符。")
    if "\x00" in patch:
        raise ValueError("Patch 不能包含 NUL 字符。")
    if "GIT binary patch" in patch or "Binary files " in patch:
        raise ValueError("禁止应用二进制 Patch。")
    if "Subproject commit " in patch:
        raise ValueError("禁止修改 Git submodule。")
    if re.search(r"^(old mode|new mode) ", patch, re.MULTILINE):
        raise ValueError("禁止修改文件权限或文件类型。")

    for header, mode in re.findall(
        r"^(new file mode|deleted file mode) (\d+)$",
        patch,
        re.MULTILINE,
    ):
        if mode in {"120000", "160000"}:
            raise ValueError("禁止修改符号链接或 Git submodule。")
        if header == "new file mode" and mode != "100644":
            raise ValueError("新文件必须是普通 UTF-8 文本文件。")
        if header == "deleted file mode" and mode not in {"100644", "100755"}:
            raise ValueError("只能删除普通文本文件。")


def _format_hunk_range(start: str, count: int) -> str:
    return start if count == 1 else f"{start},{count}"


def _recount_patch_hunks(patch: str) -> tuple[str, bool]:
    lines = patch.split("\n")
    recounted = False
    index = 0

    while index < len(lines):
        match = HUNK_HEADER_PATTERN.fullmatch(lines[index])
        if match is None:
            index += 1
            continue

        old_count = 0
        new_count = 0
        body_index = index + 1
        while body_index < len(lines):
            line = lines[body_index]
            if HUNK_HEADER_PATTERN.fullmatch(line) or line.startswith(
                "diff --git "
            ):
                break
            if not line and body_index == len(lines) - 1:
                break
            if line.startswith(" "):
                old_count += 1
                new_count += 1
            elif line.startswith("-"):
                old_count += 1
            elif line.startswith("+"):
                new_count += 1
            elif line == r"\ No newline at end of file":
                pass
            else:
                raise ValueError(
                    f"Patch hunk 第 {body_index + 1} 行缺少合法的 ASCII Diff 前缀。"
                )
            body_index += 1

        if body_index == index + 1:
            raise ValueError(f"Patch hunk 第 {index + 1} 行没有修改内容。")

        old_range = _format_hunk_range(match.group(1), old_count)
        new_range = _format_hunk_range(match.group(3), new_count)
        header = f"@@ -{old_range} +{new_range} @@{match.group(5)}"
        if header != lines[index]:
            lines[index] = header
            recounted = True
        index = body_index

    return "\n".join(lines), recounted


def _normalize_patch_for_git(patch: str) -> tuple[str, list[str]]:
    normalized = patch
    changes: list[str] = []

    if "\r" in normalized:
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
        changes.append("normalized_line_endings")

    lines = normalized.splitlines()
    if (
        len(lines) >= 3
        and lines[0].strip().lower() in PATCH_FENCE_MARKERS
        and lines[-1].strip() == "```"
    ):
        normalized = "\n".join(lines[1:-1])
        changes.append("removed_markdown_fence")

    normalized, recounted = _recount_patch_hunks(normalized)
    if recounted:
        changes.append("recounted_hunks")

    if not normalized.endswith("\n"):
        normalized += "\n"
        changes.append("added_trailing_newline")

    return normalized, changes


def _validate_existing_file(path: Path, relative: str) -> None:
    if path.is_symlink():
        raise ValueError(f"禁止修改符号链接：{relative}")
    if not path.is_file():
        raise ValueError(f"修改目标不是普通文件：{relative}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"文件超过 {MAX_FILE_BYTES} 字节限制：{relative}")
    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"文件不是有效 UTF-8 文本：{relative}") from exc
    except OSError as exc:
        raise ValueError(f"无法读取文件：{relative}：{exc}") from exc


def _capture_path_states(
    context: CodingToolContext,
    paths: list[str],
) -> list[_PathState]:
    states: list[_PathState] = []
    for relative in paths:
        target = context.repo_root / Path(*PurePosixPath(relative).parts)
        if not target.exists() and not target.is_symlink():
            states.append(_PathState(relative, False, None, None))
            continue
        _validate_existing_file(target, relative)
        states.append(
            _PathState(
                relative,
                True,
                target.read_bytes(),
                stat.S_IMODE(target.stat().st_mode),
            )
        )
    return states


def _reject_ignored_new_paths(
    context: CodingToolContext,
    states: list[_PathState],
) -> None:
    for state_item in states:
        if state_item.existed:
            continue
        result = _run_git_bytes(
            context,
            [
                "check-ignore",
                "--quiet",
                "--no-index",
                "--",
                state_item.relative_path,
            ],
        )
        if result.returncode == 0:
            raise ValueError(
                f"禁止创建被 Git 忽略的新文件：{state_item.relative_path}"
            )
        if result.returncode != 1:
            raise RuntimeError(
                f"无法检查 Git 忽略规则：{_git_error(result)}"
            )


def _remove_empty_parents(path: Path, repo_root: Path) -> None:
    current = path.parent
    while current != repo_root:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _restore_path_states(
    context: CodingToolContext,
    states: list[_PathState],
) -> None:
    for state_item in states:
        if state_item.existed:
            continue
        target = context.repo_root / Path(
            *PurePosixPath(state_item.relative_path).parts
        )
        if target.is_symlink() or target.is_file():
            target.unlink()
            _remove_empty_parents(target, context.repo_root)

    for state_item in states:
        if not state_item.existed:
            continue
        target = context.repo_root / Path(
            *PurePosixPath(state_item.relative_path).parts
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(state_item.content or b"")
        if state_item.mode is not None:
            target.chmod(state_item.mode)


def _temporary_index_environment(
    context: CodingToolContext,
    directory: str,
) -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(Path(directory) / "index")
    return env


@contextmanager
def _temporary_worktree_index(
    context: CodingToolContext,
    *,
    refresh_paths: list[str] | None = None,
) -> Iterator[dict[str, str]]:
    staged = _run_git_bytes(
        context,
        ["diff", "--cached", "--quiet", context.base_commit, "--"],
    )
    if staged.returncode == 1:
        raise ClassifiedFailure(
            make_failure("SAFETY", "Git index 已在 Coding 期间发生变化。")
        )
    if staged.returncode != 0:
        raise ClassifiedFailure(
            make_failure("INTERNAL", f"无法验证 Git index：{_git_error(staged)}")
        )

    index_result = _run_git_bytes(
        context,
        ["rev-parse", "--git-path", "index"],
    )
    if index_result.returncode != 0:
        raise ClassifiedFailure(
            make_failure(
                "ENVIRONMENT",
                f"无法定位 Git index：{_git_error(index_result)}",
            )
        )
    index_path = Path(index_result.stdout.decode("utf-8").strip())
    if not index_path.is_absolute():
        index_path = context.repo_root / index_path

    with tempfile.TemporaryDirectory(dir=context.run_dir) as temporary:
        env = _temporary_index_environment(context, temporary)
        try:
            shutil.copyfile(index_path, env["GIT_INDEX_FILE"])
        except OSError as exc:
            raise ClassifiedFailure(
                make_failure("ENVIRONMENT", f"无法复制 Git index：{exc}")
            ) from exc

        paths_to_refresh = sorted(
            set(_audited_touched_files(context))
            | set(refresh_paths or [])
        )
        tracked_paths: list[str] = []
        for relative in paths_to_refresh:
            target = context.repo_root / Path(*PurePosixPath(relative).parts)
            if not target.is_file() or target.is_symlink():
                continue
            tracked = _run_git_bytes(
                context,
                ["ls-files", "--error-unmatch", "--", relative],
                env=env,
            )
            if tracked.returncode == 0:
                tracked_paths.append(relative)
            elif tracked.returncode != 1:
                raise ClassifiedFailure(
                    make_failure(
                        "INTERNAL",
                        f"无法检查临时 index 路径：{_git_error(tracked)}",
                    )
                )

        if tracked_paths:
            flags = _run_git_bytes(
                context,
                [
                    "update-index",
                    "--no-assume-unchanged",
                    "--no-skip-worktree",
                    "--",
                    *tracked_paths,
                ],
                env=env,
            )
            if flags.returncode != 0:
                raise ClassifiedFailure(
                    make_failure(
                        "INTERNAL",
                        "无法刷新临时 index 文件状态："
                        f"{_git_error(flags)}",
                    )
                )
            refresh = _run_git_bytes(
                context,
                ["add", "--renormalize", "--", *tracked_paths],
                env=env,
            )
            if refresh.returncode != 0:
                raise ClassifiedFailure(
                    make_failure(
                        "INTERNAL",
                        "无法刷新工作区临时 index："
                        f"{_git_error(refresh)}",
                    )
                )

        add = _run_git_bytes(
            context,
            ["add", "-A", "--", "."],
            env=env,
        )
        if add.returncode != 0:
            raise ClassifiedFailure(
                make_failure(
                    "INTERNAL",
                    f"无法构建工作区临时 index：{_git_error(add)}",
                )
            )
        yield env


def _parse_name_status(data: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    fields = data.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    changed: set[str] = set()
    changes: list[dict[str, Any]] = []
    index = 0

    while index < len(fields):
        status_value = fields[index].decode("ascii")
        index += 1
        status_code = status_value[0]
        if status_code in {"R", "C"}:
            old_path = fields[index].decode("utf-8")
            new_path = fields[index + 1].decode("utf-8")
            index += 2
            changed.update((old_path, new_path))
            changes.append(
                {
                    "status": status_code,
                    "old_path": old_path,
                    "new_path": new_path,
                }
            )
        else:
            path = fields[index].decode("utf-8")
            index += 1
            changed.add(path)
            changes.append({"status": status_code, "path": path})

    return sorted(changed), changes


def _capture_changes(
    context: CodingToolContext,
    *,
    enforce_scope: bool = True,
    enforce_text: bool = True,
    refresh_paths: list[str] | None = None,
) -> _ChangeSnapshot:
    with _temporary_worktree_index(
        context,
        refresh_paths=refresh_paths,
    ) as env:
        status = _run_git_bytes(
            context,
            [
                "diff",
                "--cached",
                "--name-status",
                "-z",
                "--find-renames",
                context.base_commit,
                "--",
            ],
            env=env,
        )
        if status.returncode != 0:
            raise RuntimeError(f"无法读取修改文件：{_git_error(status)}")

        diff = _run_git_bytes(
            context,
            [
                "diff",
                "--cached",
                "--binary",
                "--full-index",
                "--find-renames",
                "--no-ext-diff",
                context.base_commit,
                "--",
            ],
            env=env,
        )
        if diff.returncode != 0:
            raise RuntimeError(f"无法生成累计 Diff：{_git_error(diff)}")

    changed_files, changes = _parse_name_status(status.stdout)
    for relative in changed_files:
        if enforce_scope:
            _validate_scoped_path(context, relative)
        else:
            _normalized_relative_path(relative)
        target = context.repo_root / Path(*PurePosixPath(relative).parts)
        if enforce_text and (target.exists() or target.is_symlink()):
            _validate_existing_file(target, relative)

    return _ChangeSnapshot(
        diff=diff.stdout.decode("utf-8", errors="strict"),
        changed_files=changed_files,
        changes=changes,
    )


def _check_patch_applies(
    context: CodingToolContext,
    patch_bytes: bytes,
    env: dict[str, str],
) -> None:
    result = _run_git_bytes(
        context,
        [
            "apply",
            "--cached",
            "--check",
            "--recount",
            "--whitespace=nowarn",
            "-",
        ],
        input_bytes=patch_bytes,
        env=env,
    )
    if result.returncode != 0:
        raise ValueError(f"Patch 无法应用：{_git_error(result)}")


def _materialize_index_paths(
    context: CodingToolContext,
    paths: list[str],
    env: dict[str, str],
) -> None:
    for relative in paths:
        present = _run_git_bytes(
            context,
            ["ls-files", "--error-unmatch", "--", relative],
            env=env,
        )
        target = context.repo_root / Path(*PurePosixPath(relative).parts)
        if present.returncode == 1:
            if target.is_symlink() or target.is_file():
                target.unlink()
                _remove_empty_parents(target, context.repo_root)
            continue
        if present.returncode != 0:
            raise RuntimeError(
                f"无法检查临时 index 路径 {relative}：{_git_error(present)}"
            )

        checkout = _run_git_bytes(
            context,
            ["checkout-index", "--force", "--", relative],
            env=env,
        )
        if checkout.returncode != 0:
            raise RuntimeError(
                f"无法从临时 index 写回 {relative}：{_git_error(checkout)}"
            )


def _coding_audit_path(context: CodingToolContext) -> Path:
    return ensure_run_logs_directory(context.run_dir) / (
        f"coding_audit_r{context.repair_round:02d}_"
        f"s{context.stage_call:02d}.jsonl"
    )


def _audited_touched_files(context: CodingToolContext) -> list[str]:
    path = _coding_audit_path(context)
    if not path.exists():
        return []

    touched: set[str] = set()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("success"):
                touched.update(entry.get("touched_files", []))
    except (OSError, ValueError, TypeError) as exc:
        raise ClassifiedFailure(
            make_failure("INTERNAL", f"无法读取 Coding 审计：{exc}")
        ) from exc
    return sorted(touched)


def get_coding_iteration_count(context: CodingToolContext) -> int:
    """返回本次 Coding 阶段已经调用 apply_patch 的次数。"""

    path = _coding_audit_path(context)
    if not path.exists():
        return 0
    return sum(
        1 for line in path.read_text(encoding="utf-8").splitlines() if line
    )


def _append_audit(context: CodingToolContext, entry: dict[str, Any]) -> int:
    path = _coding_audit_path(context)
    call_number = get_coding_iteration_count(context) + 1
    entry = {
        "repair_round": context.repair_round,
        "stage_call": context.stage_call,
        "index": call_number,
        **entry,
    }
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True))
        stream.write("\n")
    return call_number


def _apply_patch(
    context: CodingToolContext,
    patch: str,
) -> CodingToolResult:
    input_sha256 = hashlib.sha256(patch.encode("utf-8")).hexdigest()
    effective_patch: str | None = None
    effective_patch_sha256: str | None = None
    normalizations: list[str] = []
    attempt_number = get_coding_iteration_count(context) + 1
    if attempt_number > MAX_CODING_PATCH_ATTEMPTS:
        limit_failure = make_failure(
            "LIMIT",
            f"已达到最多 {MAX_CODING_PATCH_ATTEMPTS} 次 Patch 尝试，"
            "拒绝继续应用。",
        )
        try:
            _append_audit(
                context,
                {
                    "success": False,
                    "touched_files": [],
                    "changed_files": [],
                    "patch": patch,
                    "effective_patch": None,
                    "normalizations": [],
                    "input_sha256": input_sha256,
                    "effective_patch_sha256": None,
                    "cumulative_diff_sha256": None,
                    "failure": limit_failure.model_dump(mode="json"),
                },
            )
        except Exception as audit_exc:
            limit_failure = make_failure(
                "INTERNAL",
                f"{limit_failure.message}；记录 Coding 审计失败：{audit_exc}",
            )
        raise ClassifiedFailure(limit_failure)

    path_states: list[_PathState] = []
    applied = False
    touched_files: list[str] = []

    try:
        _validate_patch_headers(patch)
        effective_patch, normalizations = _normalize_patch_for_git(patch)
        _validate_patch_headers(effective_patch)
        patch_bytes = effective_patch.encode("utf-8")
        effective_patch_sha256 = hashlib.sha256(patch_bytes).hexdigest()
        touched_files = _extract_patch_paths(context, patch_bytes)
        path_states = _capture_path_states(context, touched_files)
        _reject_ignored_new_paths(context, path_states)
        with _temporary_worktree_index(context) as env:
            _check_patch_applies(context, patch_bytes, env)
            result = _run_git_bytes(
                context,
                [
                    "apply",
                    "--cached",
                    "--recount",
                    "--whitespace=nowarn",
                    "-",
                ],
                input_bytes=patch_bytes,
                env=env,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Patch 应用失败：{_git_error(result)}")
            applied = True
            _materialize_index_paths(context, touched_files, env)

        for relative in touched_files:
            target = context.repo_root / Path(*PurePosixPath(relative).parts)
            if target.exists() or target.is_symlink():
                _validate_existing_file(target, relative)

        snapshot = _capture_changes(context, refresh_paths=touched_files)
        cumulative_hash = hashlib.sha256(
            snapshot.diff.encode("utf-8")
        ).hexdigest()
        iteration = _append_audit(
            context,
            {
                "success": True,
                "touched_files": touched_files,
                "changed_files": snapshot.changed_files,
                "patch": patch,
                "effective_patch": effective_patch,
                "normalizations": normalizations,
                "input_sha256": input_sha256,
                "effective_patch_sha256": effective_patch_sha256,
                "cumulative_diff_sha256": cumulative_hash,
                "failure": None,
            },
        )
        return tool_success(
            (
                f"Patch 已应用，累计修改 {len(snapshot.changed_files)} 个文件。"
                "下一步请调用 inspect_changes；只有检查发现仍需修改时才能生成新 Patch。"
            ),
            {
                "touched_files": touched_files,
                "changed_files": snapshot.changed_files,
                "cumulative_diff_sha256": cumulative_hash,
                "iteration": iteration,
            },
        )
    except Exception as exc:
        if applied:
            try:
                _restore_path_states(context, path_states)
            except Exception as restore_exc:
                error = f"{exc}；恢复本次修改失败：{restore_exc}"
            else:
                error = str(exc)
        else:
            error = str(exc)
        try:
            _append_audit(
                context,
                {
                    "success": False,
                    "touched_files": touched_files,
                    "changed_files": [],
                    "patch": patch,
                    "effective_patch": effective_patch,
                    "normalizations": normalizations,
                    "input_sha256": input_sha256,
                    "effective_patch_sha256": effective_patch_sha256,
                    "cumulative_diff_sha256": None,
                    "failure": failure_from_exception(
                        exc,
                        "SOLUTION",
                    ).model_dump(mode="json"),
                },
            )
        except Exception as audit_exc:
            error = f"{error}；记录 Coding 审计失败：{audit_exc}"
        if attempt_number >= MAX_CODING_PATCH_ATTEMPTS:
            raise ClassifiedFailure(
                make_failure(
                    "LIMIT",
                    f"{error}；已达到最多 {MAX_CODING_PATCH_ATTEMPTS} 次 Patch 尝试。",
                )
            )
        failure = failure_from_exception(exc, "SOLUTION")
        return {
            "success": False,
            "summary": "操作失败。",
            "data": {},
            "failure": failure.model_dump(mode="json"),
            "truncated": False,
        }


def _inspect_changes(context: CodingToolContext) -> CodingToolResult:
    try:
        snapshot = _capture_changes(context)
        preview = snapshot.diff[:MAX_DIFF_PREVIEW_CHARS]
        truncated = len(snapshot.diff) > MAX_DIFF_PREVIEW_CHARS
        return tool_success(
            f"检测到 {len(snapshot.changed_files)} 个累计修改文件。",
            {
                "changed_files": snapshot.changed_files,
                "changes": snapshot.changes,
                "diff": preview,
                "diff_sha256": hashlib.sha256(
                    snapshot.diff.encode("utf-8")
                ).hexdigest(),
            },
            truncated=truncated,
        )
    except Exception as exc:
        failure = failure_from_exception(exc, "SOLUTION")
        return {
            "success": False,
            "summary": "操作失败。",
            "data": {},
            "failure": failure.model_dump(mode="json"),
            "truncated": False,
        }


def inspect_coding_changes(context: CodingToolContext) -> CodingToolResult:
    """确定性检查累计修改，并附带实际 Patch 调用次数。"""

    result = _inspect_changes(context)
    if result["success"]:
        result["data"]["iteration_count"] = get_coding_iteration_count(
            context
        )
    return result


def build_coding_tools(context: CodingToolContext) -> list[BaseTool]:
    """创建绑定仓库和修改范围的 Coding Agent 工具。"""

    @tool("apply_patch")
    def apply_patch_tool(patch: str) -> CodingToolResult:
        """应用 unified diff Patch；最多尝试 10 次，且必须使用 ASCII Diff 标记。"""

        return _apply_patch(context, patch)

    @tool("inspect_changes")
    def inspect_changes_tool() -> CodingToolResult:
        """查看当前工作区相对 base commit 的累计修改和 Diff。"""

        return inspect_coding_changes(context)

    return [apply_patch_tool, inspect_changes_tool]


def _atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _check_patch_against_base(
    context: CodingToolContext,
    patch_bytes: bytes,
) -> None:
    with tempfile.TemporaryDirectory(dir=context.run_dir) as temporary:
        env = _temporary_index_environment(context, temporary)
        read_tree = _run_git_bytes(
            context,
            ["read-tree", context.base_commit],
            env=env,
        )
        if read_tree.returncode != 0:
            raise RuntimeError(f"无法读取 base commit：{_git_error(read_tree)}")
        check = _run_git_bytes(
            context,
            ["apply", "--cached", "--check", "--recount", "-"],
            input_bytes=patch_bytes,
            env=env,
        )
        if check.returncode != 0:
            raise RuntimeError(f"最终 Patch 无法应用到 base commit：{_git_error(check)}")


def save_final_patch(
    context: CodingToolContext,
    *,
    review_approved: bool,
    tests_passed: bool,
) -> CodingToolResult:
    """在 Review 和 Test 均通过后保存唯一的最终 Patch。"""

    patch_written = False
    metadata_written = False
    patch_path = context.run_dir / "diff.patch"
    metadata_path = context.run_dir / "diff.json"
    try:
        if not review_approved or not tests_passed:
            raise ValueError("只有 Review APPROVE 且最新 Test PASSED 才能保存最终 Patch。")

        if patch_path.exists() or metadata_path.exists():
            raise ValueError("最终 Patch 已存在，禁止覆盖。")

        snapshot = _capture_changes(context)
        if not snapshot.diff.strip():
            raise ValueError("当前没有可保存的代码修改。")
        patch_bytes = snapshot.diff.encode("utf-8")
        _check_patch_against_base(context, patch_bytes)
        sha256 = hashlib.sha256(patch_bytes).hexdigest()

        _atomic_write_text(patch_path, snapshot.diff)
        patch_written = True
        _atomic_write_json(
            metadata_path,
            {
                "base_commit": context.base_commit,
                "changed_files": snapshot.changed_files,
                "sha256": sha256,
            },
        )
        metadata_written = True
        return tool_success(
            "最终 Patch 已保存。",
            {
                "patch_path": str(patch_path),
                "metadata_path": str(metadata_path),
                "changed_files": snapshot.changed_files,
                "sha256": sha256,
            },
        )
    except Exception as exc:
        if patch_written and patch_path.exists():
            patch_path.unlink()
        if metadata_written and metadata_path.exists():
            metadata_path.unlink()
        return tool_failure(str(exc), "SOLUTION")


def _current_head(context: CodingToolContext) -> str:
    result = _run_git_bytes(context, ["rev-parse", "HEAD"])
    if result.returncode != 0:
        raise RuntimeError(f"无法读取当前 HEAD：{_git_error(result)}")
    return result.stdout.decode("utf-8").strip()


def _list_untracked_files(context: CodingToolContext) -> list[str]:
    result = _run_git_bytes(
        context,
        ["ls-files", "--others", "--exclude-standard", "-z"],
    )
    if result.returncode != 0:
        raise RuntimeError(f"无法列出未跟踪文件：{_git_error(result)}")
    return [
        field.decode("utf-8")
        for field in result.stdout.split(b"\0")
        if field
    ]


def rollback_to_base(
    context: CodingToolContext,
    primary_failure: FailureInfo,
    *,
    agent_report: dict[str, Any] | None = None,
) -> CodingToolResult:
    """记录失败原因并受控恢复到本次运行的 base commit。"""

    iteration = get_coding_iteration_count(context)
    failure_path = ensure_run_logs_directory(context.run_dir) / (
        f"failure_coding_r{context.repair_round:02d}_"
        f"s{context.stage_call:02d}_i{iteration:02d}.json"
    )
    if failure_path.exists():
        return tool_failure(
            f"失败记录已存在，禁止覆盖：{failure_path.name}",
            "INTERNAL",
        )

    failure: dict[str, Any] = {
        "failure": primary_failure.model_dump(mode="json"),
        "agent_report": agent_report,
        "base_commit": context.base_commit,
        "changed_files_before_rollback": [],
        "rollback_success": False,
        "rollback_failure": None,
    }
    envelope = {
        "stage": "CODING",
        "repair_round": context.repair_round,
        "stage_call": context.stage_call,
        "index": iteration,
        "payload": failure,
    }

    try:
        snapshot = _capture_changes(
            context,
            enforce_scope=False,
            enforce_text=False,
        )
        failure["changed_files_before_rollback"] = snapshot.changed_files
        _atomic_write_json(failure_path, envelope)

        if _current_head(context) != context.base_commit:
            raise RuntimeError("当前 HEAD 已变化，拒绝自动回滚。")

        tracked_paths: list[str] = []
        for relative in snapshot.changed_files:
            exists = _run_git_bytes(
                context,
                [
                    "ls-tree",
                    "-z",
                    "--name-only",
                    context.base_commit,
                    "--",
                    relative,
                ],
            )
            if exists.returncode != 0:
                raise RuntimeError(
                    f"无法检查基线文件 {relative}：{_git_error(exists)}"
                )
            if exists.stdout.rstrip(b"\0").decode("utf-8") == relative:
                tracked_paths.append(relative)

        if tracked_paths:
            restore = _run_git_bytes(
                context,
                [
                    "restore",
                    f"--source={context.base_commit}",
                    "--staged",
                    "--worktree",
                    "--",
                    *tracked_paths,
                ],
            )
            if restore.returncode != 0:
                raise RuntimeError(
                    f"恢复跟踪文件失败：{_git_error(restore)}"
                )

        for relative in _list_untracked_files(context):
            normalized = _normalized_relative_path(relative)
            target = context.repo_root / Path(*PurePosixPath(normalized).parts)
            lexical = target.absolute()
            try:
                lexical.relative_to(context.repo_root)
            except ValueError as exc:
                raise RuntimeError(f"未跟踪路径越界，拒绝删除：{relative}") from exc
            if target.is_symlink():
                target.unlink()
                _remove_empty_parents(target, context.repo_root)
                continue

            resolved = target.resolve(strict=False)
            try:
                resolved.relative_to(context.repo_root)
            except ValueError as exc:
                raise RuntimeError(f"未跟踪路径越界，拒绝删除：{relative}") from exc
            if target.is_file():
                target.unlink()
                _remove_empty_parents(target, context.repo_root)

        remaining = _run_git_bytes(
            context,
            ["status", "--porcelain", "--untracked-files=all"],
        )
        if remaining.returncode != 0:
            raise RuntimeError(f"无法验证回滚结果：{_git_error(remaining)}")
        if remaining.stdout.strip():
            raise RuntimeError("回滚后工作区仍存在未提交修改。")

        for artifact in (context.run_dir / "diff.patch", context.run_dir / "diff.json"):
            if artifact.exists():
                artifact.unlink()

        failure["rollback_success"] = True
        _atomic_write_json(failure_path, envelope)
        return tool_success(
            "已记录失败原因并恢复到 base commit。",
            {
                "failure_path": str(failure_path),
                "changed_files": snapshot.changed_files,
            },
        )
    except Exception as exc:
        rollback_failure = failure_from_exception(exc, "SAFETY")
        failure["rollback_failure"] = rollback_failure.model_dump(mode="json")
        _atomic_write_json(failure_path, envelope)
        return {
            "success": False,
            "summary": "操作失败。",
            "data": {},
            "failure": rollback_failure.model_dump(mode="json"),
            "truncated": False,
        }
