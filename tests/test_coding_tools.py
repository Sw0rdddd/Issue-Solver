import json
import subprocess
from pathlib import Path

import pytest

from tools.coding import (
    CodingToolContext,
    build_coding_tools,
    get_coding_iteration_count,
    inspect_coding_changes,
    rollback_to_base,
    save_final_patch,
)


def run_git(repo_path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def make_context(
    git_repo: Path,
    *,
    allowed_paths: list[str] | None = None,
    repair_round: int = 1,
    stage_call: int = 1,
) -> CodingToolContext:
    return CodingToolContext.create(
        repo_root=git_repo,
        base_commit=run_git(git_repo, "rev-parse", "HEAD"),
        run_dir=git_repo.parent / "run",
        allowed_paths=allowed_paths or ["tracked.txt", "src/", "tests/"],
        repair_round=repair_round,
        stage_call=stage_call,
    )


def tools_by_name(context: CodingToolContext) -> dict[str, object]:
    return {tool.name: tool for tool in build_coding_tools(context)}


def modify_patch(old: str = "initial", new: str = "changed") -> str:
    return f"""diff --git a/tracked.txt b/tracked.txt
--- a/tracked.txt
+++ b/tracked.txt
@@ -1 +1 @@
-{old}
+{new}
"""


def add_patch(path: str = "src/new.py", content: str = "print('new')") -> str:
    return f"""diff --git a/{path} b/{path}
new file mode 100644
--- /dev/null
+++ b/{path}
@@ -0,0 +1 @@
+{content}
"""


def delete_patch() -> str:
    return """diff --git a/tracked.txt b/tracked.txt
deleted file mode 100644
--- a/tracked.txt
+++ /dev/null
@@ -1 +0,0 @@
-initial
"""


def rename_patch() -> str:
    return """diff --git a/tracked.txt b/src/renamed.txt
similarity index 100%
rename from tracked.txt
rename to src/renamed.txt
"""


def test_agent_tools_do_not_expose_bound_context(git_repo: Path) -> None:
    context = make_context(git_repo)
    tools = tools_by_name(context)

    assert set(tools) == {"apply_patch", "inspect_changes"}
    assert set(tools["apply_patch"].args) == {"patch"}
    assert tools["inspect_changes"].args == {}


def test_context_requires_clean_repository(git_repo: Path) -> None:
    (git_repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(ValueError, match="干净"):
        make_context(git_repo)


def test_context_can_explicitly_resume_existing_changes(git_repo: Path) -> None:
    (git_repo / "tracked.txt").write_text("existing\n", encoding="utf-8")

    context = CodingToolContext.create(
        repo_root=git_repo,
        base_commit=run_git(git_repo, "rev-parse", "HEAD"),
        run_dir=git_repo.parent / "resume-run",
        allowed_paths=["tracked.txt"],
        allow_existing_changes=True,
    )

    inspected = inspect_coding_changes(context)
    assert inspected["success"] is True
    assert inspected["data"]["changed_files"] == ["tracked.txt"]


def test_context_rejects_run_directory_inside_repository(git_repo: Path) -> None:
    with pytest.raises(ValueError, match="运行目录不能位于目标仓库内"):
        CodingToolContext.create(
            repo_root=git_repo,
            base_commit=run_git(git_repo, "rev-parse", "HEAD"),
            run_dir=git_repo / ".issue-solver-runs" / "run",
            allowed_paths=["tracked.txt"],
        )


def test_apply_patch_modifies_real_file_and_inspects_diff(
    git_repo: Path,
) -> None:
    context = make_context(git_repo)
    tools = tools_by_name(context)

    result = tools["apply_patch"].invoke({"patch": modify_patch()})
    inspected = tools["inspect_changes"].invoke({})

    assert result["success"] is True
    assert (git_repo / "tracked.txt").read_text(encoding="utf-8") == "changed\n"
    assert inspected["success"] is True
    assert inspected["data"]["changed_files"] == ["tracked.txt"]
    assert "-initial" in inspected["data"]["diff"]
    assert "+changed" in inspected["data"]["diff"]
    assert not (context.run_dir / "diff.patch").exists()


@pytest.mark.parametrize(
    ("patch", "expected_paths"),
    [
        (add_patch(), ["src/new.py"]),
        (delete_patch(), ["tracked.txt"]),
        (rename_patch(), ["src/renamed.txt", "tracked.txt"]),
    ],
)
def test_apply_patch_supports_text_file_operations(
    git_repo: Path,
    patch: str,
    expected_paths: list[str],
) -> None:
    (git_repo / "src").mkdir()
    context = make_context(git_repo)
    tools = tools_by_name(context)

    result = tools["apply_patch"].invoke({"patch": patch})
    inspected = tools["inspect_changes"].invoke({})

    assert result["success"] is True
    assert inspected["data"]["changed_files"] == expected_paths


def test_apply_patch_allows_multiple_cumulative_edits(git_repo: Path) -> None:
    context = make_context(git_repo)
    apply_patch_tool = tools_by_name(context)["apply_patch"]

    first = apply_patch_tool.invoke({"patch": modify_patch()})
    second = apply_patch_tool.invoke(
        {"patch": modify_patch(old="changed", new="final")}
    )

    assert first["success"] is True
    assert second["success"] is True
    assert (git_repo / "tracked.txt").read_text(encoding="utf-8") == "final\n"
    assert get_coding_iteration_count(context) == 2


def test_apply_patch_rejects_path_outside_allowed_scope(git_repo: Path) -> None:
    context = make_context(git_repo, allowed_paths=["tests/"])
    result = tools_by_name(context)["apply_patch"].invoke(
        {"patch": modify_patch()}
    )

    assert result["success"] is False
    assert "允许修改范围" in result["error"]
    assert (git_repo / "tracked.txt").read_text(encoding="utf-8") == "initial\n"


def test_apply_patch_rejects_protected_path(git_repo: Path) -> None:
    context = make_context(git_repo, allowed_paths=[".git/"])
    patch = add_patch(path=".git/agent.txt", content="unsafe")

    result = tools_by_name(context)["apply_patch"].invoke({"patch": patch})

    assert result["success"] is False
    assert "受保护路径" in result["error"]
    assert not (git_repo / ".git" / "agent.txt").exists()


def test_apply_patch_rejects_conda_environment_path(git_repo: Path) -> None:
    context = make_context(git_repo, allowed_paths=[".conda/"])
    result = tools_by_name(context)["apply_patch"].invoke(
        {"patch": add_patch(path=".conda/agent.txt", content="unsafe")}
    )

    assert result["success"] is False
    assert "受保护路径" in result["error"]


def test_apply_patch_failure_is_atomic(git_repo: Path) -> None:
    context = make_context(git_repo)
    patch = modify_patch() + """diff --git a/missing.txt b/missing.txt
--- a/missing.txt
+++ b/missing.txt
@@ -1 +1 @@
-missing
+changed
"""

    result = tools_by_name(context)["apply_patch"].invoke({"patch": patch})

    assert result["success"] is False
    assert (git_repo / "tracked.txt").read_text(encoding="utf-8") == "initial\n"
    assert not (git_repo / "missing.txt").exists()


@pytest.mark.parametrize(
    ("patch", "error_text"),
    [
        ("GIT binary patch\nliteral 0\n", "二进制"),
        (
            """diff --git a/tracked.txt b/tracked.txt
old mode 100644
new mode 100755
""",
            "权限或文件类型",
        ),
    ],
)
def test_apply_patch_rejects_unsupported_patch_types(
    git_repo: Path,
    patch: str,
    error_text: str,
) -> None:
    context = make_context(git_repo)

    result = tools_by_name(context)["apply_patch"].invoke({"patch": patch})

    assert result["success"] is False
    assert error_text in result["error"]


def test_apply_patch_rejects_symlink_target(git_repo: Path) -> None:
    context = make_context(git_repo)
    outside = git_repo.parent / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    link = git_repo / "src"
    try:
        link.symlink_to(outside.parent, target_is_directory=True)
    except OSError:
        pytest.skip("当前环境不允许创建符号链接")

    result = tools_by_name(context)["apply_patch"].invoke(
        {"patch": add_patch(path="src/outside.txt", content="changed")}
    )

    assert result["success"] is False
    assert "符号链接" in result["error"] or "仓库之外" in result["error"]
    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_apply_patch_rejects_new_ignored_file(git_repo: Path) -> None:
    (git_repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    run_git(git_repo, "add", ".gitignore")
    run_git(
        git_repo,
        "-c",
        "user.name=Issue Solver Tests",
        "-c",
        "user.email=tests@example.com",
        "commit",
        "-m",
        "add ignore rule",
    )
    context = make_context(git_repo, allowed_paths=["ignored.txt"])

    result = tools_by_name(context)["apply_patch"].invoke(
        {"patch": add_patch(path="ignored.txt", content="hidden")}
    )

    assert result["success"] is False
    assert "Git 忽略" in result["error"]
    assert not (git_repo / "ignored.txt").exists()


def test_apply_patch_rejects_patch_over_size_limit(git_repo: Path) -> None:
    context = make_context(git_repo)

    result = tools_by_name(context)["apply_patch"].invoke(
        {"patch": "x" * 100_001}
    )

    assert result["success"] is False
    assert "100000" in result["error"]


def test_inspect_changes_truncates_large_diff(git_repo: Path) -> None:
    context = make_context(git_repo)
    tools = tools_by_name(context)
    tools["apply_patch"].invoke(
        {"patch": modify_patch(new="x" * 25_000)}
    )

    result = tools["inspect_changes"].invoke({})

    assert result["success"] is True
    assert result["truncated"] is True
    assert len(result["data"]["diff"]) <= 20_000


def test_save_final_patch_requires_approval_and_passed_tests(
    git_repo: Path,
) -> None:
    context = make_context(git_repo)
    tools_by_name(context)["apply_patch"].invoke({"patch": modify_patch()})

    rejected = save_final_patch(
        context,
        review_approved=False,
        tests_passed=True,
    )

    assert rejected["success"] is False
    assert not (context.run_dir / "diff.patch").exists()


def test_save_final_patch_writes_patch_and_metadata(git_repo: Path) -> None:
    context = make_context(git_repo)
    tools_by_name(context)["apply_patch"].invoke({"patch": modify_patch()})

    result = save_final_patch(
        context,
        review_approved=True,
        tests_passed=True,
    )

    patch_path = context.run_dir / "diff.patch"
    metadata = json.loads(
        (context.run_dir / "diff.json").read_text(encoding="utf-8")
    )
    assert result["success"] is True
    assert patch_path.is_file()
    assert metadata["base_commit"] == context.base_commit
    assert metadata["changed_files"] == ["tracked.txt"]
    assert metadata["sha256"] == result["data"]["sha256"]


def test_rollback_restores_base_and_records_reason(git_repo: Path) -> None:
    (git_repo / "src").mkdir()
    context = make_context(git_repo)
    apply_patch_tool = tools_by_name(context)["apply_patch"]
    apply_patch_tool.invoke({"patch": modify_patch()})
    apply_patch_tool.invoke({"patch": add_patch()})

    result = rollback_to_base(context, "达到最大循环次数")

    failure = json.loads(
        (
            context.run_dir / "failure_coding_r01_s01_i02.json"
        ).read_text(encoding="utf-8")
    )["payload"]
    assert result["success"] is True
    assert failure["reason"] == "达到最大循环次数"
    assert failure["rollback_success"] is True
    assert (git_repo / "tracked.txt").read_text(encoding="utf-8") == "initial\n"
    assert not (git_repo / "src" / "new.py").exists()
    assert run_git(git_repo, "status", "--porcelain") == ""
    assert not (context.run_dir / "diff.patch").exists()


@pytest.mark.parametrize("patch", [delete_patch(), rename_patch()])
def test_rollback_restores_deleted_or_renamed_file(
    git_repo: Path,
    patch: str,
) -> None:
    (git_repo / "src").mkdir()
    context = make_context(git_repo)
    tools_by_name(context)["apply_patch"].invoke({"patch": patch})

    result = rollback_to_base(context, "运行失败")

    assert result["success"] is True
    assert (git_repo / "tracked.txt").read_text(encoding="utf-8") == "initial\n"
    assert not (git_repo / "src" / "renamed.txt").exists()
    assert run_git(git_repo, "status", "--porcelain") == ""


def test_rollback_restores_changes_outside_agent_scope(git_repo: Path) -> None:
    outside_scope = git_repo / "outside_scope.txt"
    outside_scope.write_text("base\n", encoding="utf-8")
    run_git(git_repo, "add", "outside_scope.txt")
    run_git(
        git_repo,
        "-c",
        "user.name=Issue Solver Tests",
        "-c",
        "user.email=tests@example.com",
        "commit",
        "-m",
        "add outside scope file",
    )
    context = make_context(git_repo, allowed_paths=["tracked.txt"])
    outside_scope.write_text("changed by test\n", encoding="utf-8")
    (git_repo / "test-created.txt").write_text("temporary\n", encoding="utf-8")

    result = rollback_to_base(context, "测试修改了工作区")

    assert result["success"] is True
    assert outside_scope.read_text(encoding="utf-8") == "base\n"
    assert not (git_repo / "test-created.txt").exists()
    assert run_git(git_repo, "status", "--porcelain") == ""


def test_rollback_removes_untracked_symlink_without_touching_target(
    git_repo: Path,
) -> None:
    context = make_context(git_repo)
    outside = git_repo.parent / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    link = git_repo / "agent-link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("当前环境不允许创建符号链接")

    result = rollback_to_base(context, "运行失败")

    assert result["success"] is True
    assert not link.exists()
    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_audit_log_tracks_hashes_without_patch_content(git_repo: Path) -> None:
    context = make_context(git_repo, repair_round=2, stage_call=3)
    patch = modify_patch()

    tools_by_name(context)["apply_patch"].invoke({"patch": patch})

    audit = (context.run_dir / "coding_audit_r02_s03.jsonl").read_text(
        encoding="utf-8"
    )
    entry = json.loads(audit.strip())
    assert entry["repair_round"] == 2
    assert entry["stage_call"] == 3
    assert entry["index"] == 1
    assert entry["success"] is True
    assert entry["input_sha256"]
    assert entry["cumulative_diff_sha256"]
    assert patch not in audit


def test_failed_patch_also_increments_iteration(git_repo: Path) -> None:
    context = make_context(git_repo)
    apply_patch = tools_by_name(context)["apply_patch"]

    apply_patch.invoke({"patch": "invalid patch"})
    apply_patch.invoke({"patch": modify_patch()})

    assert get_coding_iteration_count(context) == 2
    entries = [
        json.loads(line)
        for line in (
            context.run_dir / "coding_audit_r01_s01.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert [entry["index"] for entry in entries] == [1, 2]
    assert [entry["success"] for entry in entries] == [False, True]


def test_deterministic_inspection_includes_patch_iterations(
    git_repo: Path,
) -> None:
    context = make_context(git_repo)
    tools_by_name(context)["apply_patch"].invoke({"patch": modify_patch()})

    result = inspect_coding_changes(context)

    assert result["success"] is True
    assert result["data"]["changed_files"] == ["tracked.txt"]
    assert result["data"]["iteration_count"] == 1
