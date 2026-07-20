import json
import subprocess
from pathlib import Path

import pytest

from nodes import coding
from schemas.coding_result import CodingResult
from schemas.coding_task import CodingTask
from schemas.explore_report import ExploreReport
from schemas.issue_specification import IssueSpec
from tools.coding import build_coding_tools


PATCH = """diff --git a/tracked.txt b/tracked.txt
--- a/tracked.txt
+++ b/tracked.txt
@@ -1 +1 @@
-initial
+changed
"""


def git_output(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def make_task() -> CodingTask:
    return CodingTask(
        objective="修改 tracked.txt",
        acceptance_criteria=["内容变为 changed"],
        relevant_files=["tracked.txt"],
        root_cause="原始内容不正确",
        allowed_scope=["tracked.txt"],
        test_targets=["tests/test_tracked.py"],
    )


def make_state(git_repo: Path, run_dir: Path) -> dict:
    return {
        "issue": IssueSpec(title="修改内容", body="将 initial 改为 changed"),
        "coding_task": make_task(),
        "explore_reports": [
            ExploreReport(
                focus="定位文件",
                relevant_files=["tracked.txt"],
                relevant_symbols=[],
                findings=["内容仍为 initial"],
                root_cause="原始内容不正确",
                test_targets=[],
                unknowns=[],
            )
        ],
        "current_summary": "根因已经确认",
        "repo_path": str(git_repo),
        "base_commit": git_output(git_repo, "rev-parse", "HEAD"),
        "run_dir": str(run_dir),
        "cycle": 0,
        "coding_stage_call": 2,
    }


class PatchAgent:
    def __init__(
        self,
        context,
        result: CodingResult,
        patches: list[str],
    ) -> None:
        self.context = context
        self.result = result
        self.patches = patches
        self.calls: list[dict] = []

    def invoke(self, payload: dict) -> dict:
        self.calls.append(payload)
        apply_patch = {
            tool.name: tool for tool in build_coding_tools(self.context)
        }["apply_patch"]
        for patch in self.patches:
            apply_patch.invoke({"patch": patch})
        return {"structured_response": self.result}


def test_coding_node_saves_task_and_final_result_with_rsi(
    monkeypatch,
    git_repo: Path,
) -> None:
    run_dir = git_repo.parent / "coding-run"
    agents: list[PatchAgent] = []

    def fake_build_agent(model, context):
        agent = PatchAgent(
            context,
            CodingResult(
                success=True,
                changed_files=["tracked.txt"],
                summary="更新了内容",
                diff_path=None,
                validation=["检查累计 Diff"],
                remaining_risks=[],
            ),
            [PATCH],
        )
        agents.append(agent)
        return agent

    monkeypatch.setattr(coding, "build_coding_agent", fake_build_agent)
    node = coding.build_coding_node(object())

    result = node(make_state(git_repo, run_dir))

    assert result["phase"] == "REVIEW"
    assert result["changed_files"] == ["tracked.txt"]
    assert result["coding_iteration"] == 1
    assert git_output(git_repo, "status", "--porcelain") == "M tracked.txt"
    task_path = run_dir / "logs" / "coding_task_r01_s02_i00.json"
    result_path = run_dir / "logs" / "coding_result_r01_s02_i01.json"
    assert json.loads(task_path.read_text(encoding="utf-8"))["payload"] == (
        make_task().model_dump()
    )
    saved_result = json.loads(result_path.read_text(encoding="utf-8"))
    assert saved_result["repair_round"] == 1
    assert saved_result["stage_call"] == 2
    assert saved_result["index"] == 1
    assert saved_result["payload"]["success"] is True
    assert "修改 tracked.txt" in agents[0].calls[0]["messages"][0]["content"]


def test_coding_node_program_generates_error_and_rolls_back(
    monkeypatch,
    git_repo: Path,
) -> None:
    run_dir = git_repo.parent / "coding-failure-run"

    def fake_build_agent(model, context):
        return PatchAgent(
            context,
            CodingResult(
                success=False,
                changed_files=["tracked.txt"],
                summary="模型认为仍未完成",
                diff_path=None,
                validation=["Patch 已应用"],
                remaining_risks=["仍需修改其他文件"],
            ),
            [PATCH, "invalid patch"],
        )

    monkeypatch.setattr(coding, "build_coding_agent", fake_build_agent)
    node = coding.build_coding_node(object())

    result = node(make_state(git_repo, run_dir))

    assert result["status"] == "FAILED"
    assert result["phase"] == "CODE"
    assert result["coding_iteration"] == 2
    assert "Coding Agent 报告任务未完成" in result["error"]
    assert git_output(git_repo, "status", "--porcelain") == ""
    failure_path = run_dir / "logs" / "failure_coding_r01_s02_i02.json"
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["payload"]["reason"] == result["error"]
    assert failure["payload"]["agent_report"] == {
        "summary": "模型认为仍未完成",
        "remaining_risks": ["仍需修改其他文件"],
    }
    assert failure["payload"]["rollback_success"] is True
    assert not list((run_dir / "logs").glob("coding_result_*.json"))


def test_coding_node_rejects_agent_changed_files_and_rolls_back(
    monkeypatch,
    git_repo: Path,
) -> None:
    run_dir = git_repo.parent / "coding-mismatch-run"

    def fake_build_agent(model, context):
        return PatchAgent(
            context,
            CodingResult(
                success=True,
                changed_files=["wrong.py"],
                summary="错误的文件列表",
                diff_path=None,
                validation=["检查累计 Diff"],
                remaining_risks=[],
            ),
            [PATCH],
        )

    monkeypatch.setattr(coding, "build_coding_agent", fake_build_agent)
    result = coding.build_coding_node(object())(
        make_state(git_repo, run_dir)
    )

    assert result["status"] == "FAILED"
    assert "changed_files 与实际修改不一致" in result["error"]
    assert git_output(git_repo, "status", "--porcelain") == ""


def test_coding_node_preserves_task_when_agent_raises(
    monkeypatch,
    git_repo: Path,
) -> None:
    run_dir = git_repo.parent / "coding-agent-error-run"

    class FailingAgent:
        def invoke(self, payload: dict) -> dict:
            raise RuntimeError("模型不可用")

    monkeypatch.setattr(
        coding,
        "build_coding_agent",
        lambda model, context: FailingAgent(),
    )

    result = coding.build_coding_node(object())(
        make_state(git_repo, run_dir)
    )

    assert result["status"] == "FAILED"
    assert "模型不可用" in result["error"]
    assert (
        run_dir / "logs" / "coding_task_r01_s02_i00.json"
    ).is_file()
    assert (
        run_dir / "logs" / "failure_coding_r01_s02_i00.json"
    ).is_file()


def test_coding_node_stops_after_tenth_failed_patch(
    monkeypatch,
    git_repo: Path,
) -> None:
    run_dir = git_repo.parent / "coding-patch-limit-run"

    def fake_build_agent(model, context):
        return PatchAgent(
            context,
            CodingResult(
                success=False,
                changed_files=[],
                summary="未能生成有效 Patch",
                diff_path=None,
                validation=[],
                remaining_risks=["Patch 格式无效"],
            ),
            [f"invalid patch {index}" for index in range(10)],
        )

    monkeypatch.setattr(coding, "build_coding_agent", fake_build_agent)

    result = coding.build_coding_node(object())(
        make_state(git_repo, run_dir)
    )

    assert result["status"] == "FAILED"
    assert result["coding_iteration"] == 10
    assert "最多 10 次 Patch 尝试" in result["error"]
    assert git_output(git_repo, "status", "--porcelain") == ""
    audit_path = run_dir / "logs" / "coding_audit_r01_s02.jsonl"
    entries = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(entries) == 10
    assert entries[-1]["patch"] == "invalid patch 9"
    assert (
        run_dir / "logs" / "failure_coding_r01_s02_i10.json"
    ).is_file()


@pytest.mark.parametrize(
    ("patches", "diff_path", "error_text"),
    [
        ([], None, "未产生代码修改"),
        ([PATCH], "diff.patch", "diff_path 必须为 null"),
    ],
)
def test_coding_node_rejects_invalid_success_claims(
    monkeypatch,
    git_repo: Path,
    patches: list[str],
    diff_path: str | None,
    error_text: str,
) -> None:
    run_dir = git_repo.parent / f"invalid-success-{len(patches)}"

    def fake_build_agent(model, context):
        return PatchAgent(
            context,
            CodingResult(
                success=True,
                changed_files=["tracked.txt"] if patches else [],
                summary="声称已经完成",
                diff_path=diff_path,
                validation=["声称检查过"],
                remaining_risks=[],
            ),
            patches,
        )

    monkeypatch.setattr(coding, "build_coding_agent", fake_build_agent)

    result = coding.build_coding_node(object())(
        make_state(git_repo, run_dir)
    )

    assert result["status"] == "FAILED"
    assert error_text in result["error"]
    assert git_output(git_repo, "status", "--porcelain") == ""
