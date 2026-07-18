import json
from pathlib import Path

import pytest

from schemas.explore_report import ExploreReport
from services.artifacts import (
    write_round_artifact,
    write_run_artifact,
    write_stage_artifact,
)


def test_write_run_artifact_has_no_round_coordinates(tmp_path: Path) -> None:
    path = write_run_artifact(
        run_dir=tmp_path,
        kind="environment_result",
        stage="INITIALIZE",
        payload={"kind": "VENV"},
    )

    assert path.name == "environment_result.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "stage": "INITIALIZE",
        "payload": {"kind": "VENV"},
    }


def test_write_stage_artifact_records_rsi_and_payload(tmp_path: Path) -> None:
    report = ExploreReport(
        focus="定位入口",
        relevant_files=["app.py"],
        relevant_symbols=[],
        findings=[],
        root_cause="",
        test_targets=[],
        unknowns=[],
    )

    path = write_stage_artifact(
        run_dir=tmp_path,
        kind="explore",
        stage="EXPLORE",
        repair_round=1,
        stage_call=2,
        index=3,
        payload=report,
    )

    assert path.name == "explore_r01_s02_i03.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "stage": "EXPLORE",
        "repair_round": 1,
        "stage_call": 2,
        "index": 3,
        "payload": report.model_dump(),
    }


def test_write_round_artifact_uses_only_repair_round(tmp_path: Path) -> None:
    path = write_round_artifact(
        run_dir=tmp_path,
        kind="review_result",
        stage="REVIEW",
        repair_round=2,
        payload={"verdict": "APPROVE"},
    )

    assert path.name == "review_result_r02.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "stage": "REVIEW",
        "repair_round": 2,
        "payload": {"verdict": "APPROVE"},
    }

    with pytest.raises(FileExistsError):
        write_round_artifact(
            run_dir=tmp_path,
            kind="review_result",
            stage="REVIEW",
            repair_round=2,
            payload={},
        )


def test_write_stage_artifact_never_overwrites(tmp_path: Path) -> None:
    kwargs = {
        "run_dir": tmp_path,
        "kind": "coding_task",
        "stage": "CODING",
        "repair_round": 1,
        "stage_call": 1,
        "index": 0,
        "payload": {"objective": "修复问题"},
    }
    write_stage_artifact(**kwargs)

    with pytest.raises(FileExistsError):
        write_stage_artifact(**kwargs)


@pytest.mark.parametrize(
    ("repair_round", "stage_call", "index"),
    [(0, 1, 0), (1, 0, 0), (1, 1, -1)],
)
def test_write_stage_artifact_rejects_invalid_coordinates(
    tmp_path: Path,
    repair_round: int,
    stage_call: int,
    index: int,
) -> None:
    with pytest.raises(ValueError):
        write_stage_artifact(
            run_dir=tmp_path,
            kind="explore",
            stage="EXPLORE",
            repair_round=repair_round,
            stage_call=stage_call,
            index=index,
            payload={},
        )
