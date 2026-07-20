import json
from pathlib import Path

import pytest

from nodes.review import build_review_node
from schemas.coding_result import CodingResult
from schemas.coding_task import CodingTask
from schemas.issue_specification import IssueSpec
from schemas.review_result import ReviewResult


class FakeReviewAgent:
    def __init__(self, response: object = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def invoke(self, payload: dict) -> object:
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        return self.response


def make_state(run_dir: Path) -> dict:
    return {
        "run_id": "run_test",
        "phase": "REVIEW",
        "status": "RUNNING",
        "cycle": 1,
        "repair_round": 2,
        "repo_path": "C:/repo",
        "base_commit": "abc123",
        "run_dir": str(run_dir),
        "issue_input": "搜索失败",
        "issue": IssueSpec(title="搜索失败", body="应忽略大小写"),
        "coding_task": CodingTask(
            objective="修复搜索",
            acceptance_criteria=["忽略大小写"],
            relevant_files=["search.py"],
            root_cause="直接比较原字符串",
            allowed_scope=["search.py"],
            test_targets=["tests/test_search.py"],
        ),
        "coding_result": CodingResult(
            success=True,
            changed_files=["search.py"],
            summary="归一化大小写",
            diff_path=None,
            validation=["检查 Diff"],
            remaining_risks=[],
        ),
    }


@pytest.mark.parametrize("verdict", ["APPROVE", "REQUEST_CHANGES"])
def test_review_node_saves_round_result_and_enters_test(
    tmp_path: Path,
    verdict: str,
) -> None:
    review_result = ReviewResult(
        verdict=verdict,
        issues=[] if verdict == "APPROVE" else ["缺少边界处理"],
        suggestions=[],
        remaining_risks=[],
    )
    agent = FakeReviewAgent({"structured_response": review_result})

    result = build_review_node(agent)(make_state(tmp_path))

    assert result["phase"] == "TEST"
    assert result["review_result"] == review_result
    artifact = json.loads(
        (tmp_path / "logs" / "review_result_r02.json").read_text(
            encoding="utf-8"
        )
    )
    assert artifact["payload"] == review_result.model_dump()
    assert "abc123" in agent.calls[0]["messages"][0]["content"]


@pytest.mark.parametrize(
    "agent",
    [
        FakeReviewAgent({"structured_response": object()}),
        FakeReviewAgent(error=RuntimeError("模型不可用")),
    ],
)
def test_review_node_failure_logs_and_requests_rollback(
    tmp_path: Path,
    agent: FakeReviewAgent,
) -> None:
    result = build_review_node(agent)(make_state(tmp_path))

    assert result["status"] == "FAILED"
    assert result["phase"] == "REVIEW"
    assert result["rollback_prompt_required"] is True
    assert result["rollback_reason"] == result["error"]
    failure = json.loads(
        (tmp_path / "logs" / "failure_review_r02.json").read_text(
            encoding="utf-8"
        )
    )
    assert failure["payload"]["reason"] == result["error"]
