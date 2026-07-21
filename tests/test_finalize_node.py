import json
import subprocess
from pathlib import Path

from nodes.finalize import build_finalize_node
from schemas.failure import make_failure
from schemas.review_result import ReviewResult
from schemas.test_result import TestResult as ExecutionResult
from tools.coding import CodingToolContext, build_coding_tools


PATCH = """diff --git a/tracked.txt b/tracked.txt
--- a/tracked.txt
+++ b/tracked.txt
@@ -1 +1 @@
-initial
+changed
"""


def git_output(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def prepared_state(git_repo: Path, run_dir: Path) -> dict:
    base_commit = git_output(git_repo, "rev-parse", "HEAD")
    context = CodingToolContext.create(
        repo_root=git_repo,
        base_commit=base_commit,
        run_dir=run_dir,
        allowed_paths=["tracked.txt"],
        repair_round=1,
        stage_call=1,
    )
    apply_patch = {tool.name: tool for tool in build_coding_tools(context)}[
        "apply_patch"
    ]
    assert apply_patch.invoke({"patch": PATCH})["success"] is True
    test_result = ExecutionResult(
        command="pytest -q",
        resolved_command=["C:/repo/.venv/Scripts/python.exe", "-m", "pytest", "-q"],
        cwd=str(git_repo),
        python_executable="C:/repo/.venv/Scripts/python.exe",
        status="PASSED",
        exit_code=0,
        duration=0.1,
        stdout_path="stdout.log",
        stderr_path="stderr.log",
        output_tail="[stdout] passed",
    )
    return {
        "run_id": "run_test",
        "phase": "FINALIZE",
        "status": "RUNNING",
        "cycle": 1,
        "repair_round": 1,
        "repo_path": str(git_repo),
        "base_commit": base_commit,
        "run_dir": str(run_dir),
        "issue_input": "test",
        "changed_files": ["tracked.txt"],
        "coding_stage_call": 1,
        "review_result": ReviewResult(
            verdict="APPROVE",
            issues=[],
            suggestions=[],
            remaining_risks=[],
        ),
        "latest_test_results": [test_result],
    }


def test_finalize_saves_patch_only_after_review_and_tests_pass(
    git_repo: Path,
) -> None:
    run_dir = git_repo.parent / "finalize-success"
    state = prepared_state(git_repo, run_dir)
    state["next_action"] = "FINISH"

    result = build_finalize_node()(state)

    assert result["status"] == "FINISHED"
    assert result["diff_path"] == str(run_dir / "diff.patch")
    assert (run_dir / "diff.patch").is_file()
    artifact = json.loads(
        (run_dir / "logs" / "finalize_result_r01.json").read_text(
            encoding="utf-8"
        )
    )
    assert artifact["payload"]["status"] == "FINISHED"


def test_finalize_rolls_back_when_limit_requires_it(git_repo: Path) -> None:
    run_dir = git_repo.parent / "finalize-rollback"
    state = prepared_state(git_repo, run_dir)
    state.update(
        {
            "next_action": "FAILED",
            "status": "FAILED",
            "rollback_required": True,
            "failure": make_failure("LIMIT", "达到最大循环次数 5"),
        }
    )

    result = build_finalize_node()(state)

    assert result["status"] == "FAILED"
    assert result["changed_files"] == []
    assert git_output(git_repo, "status", "--porcelain") == ""
    artifact = json.loads(
        (run_dir / "logs" / "finalize_result_r01.json").read_text(
            encoding="utf-8"
        )
    )
    assert artifact["payload"]["rollback_success"] is True
