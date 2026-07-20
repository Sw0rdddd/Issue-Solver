import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel


ARTIFACT_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def _json_payload(payload: Any) -> Any:
    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="json")
    if isinstance(payload, list):
        return [_json_payload(item) for item in payload]
    if isinstance(payload, dict):
        return {key: _json_payload(value) for key, value in payload.items()}
    return payload


def _artifact_directory(run_dir: str | Path) -> Path:
    directory = Path(run_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _write_json_exclusive(path: Path, value: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def write_run_artifact(
    *,
    run_dir: str | Path,
    kind: str,
    stage: str,
    payload: Any,
) -> Path:
    """保存不带轮次坐标且不可覆盖的运行级 JSON 产物。"""

    if not ARTIFACT_KIND_PATTERN.fullmatch(kind):
        raise ValueError(f"无效的产物类型：{kind}")
    path = _artifact_directory(run_dir) / f"{kind}.json"
    _write_json_exclusive(
        path,
        {
            "stage": stage,
            "payload": _json_payload(payload),
        },
    )
    return path


def write_round_artifact(
    *,
    run_dir: str | Path,
    kind: str,
    stage: str,
    repair_round: int,
    payload: Any,
) -> Path:
    """保存只按 repair round 命名且不可覆盖的 JSON 产物。"""

    if not ARTIFACT_KIND_PATTERN.fullmatch(kind):
        raise ValueError(f"无效的产物类型：{kind}")
    if repair_round < 1:
        raise ValueError("repair_round 必须大于 0。")

    path = _artifact_directory(run_dir) / f"{kind}_r{repair_round:02d}.json"
    _write_json_exclusive(
        path,
        {
            "stage": stage,
            "repair_round": repair_round,
            "payload": _json_payload(payload),
        },
    )
    return path


def write_stage_artifact(
    *,
    run_dir: str | Path,
    kind: str,
    stage: str,
    repair_round: int,
    stage_call: int,
    index: int,
    payload: Any,
) -> Path:
    """以统一 r/s/i 坐标保存不可覆盖的阶段 JSON 产物。"""

    if not ARTIFACT_KIND_PATTERN.fullmatch(kind):
        raise ValueError(f"无效的产物类型：{kind}")
    if repair_round < 1:
        raise ValueError("repair_round 必须大于 0。")
    if stage_call < 1:
        raise ValueError("stage_call 必须大于 0。")
    if index < 0:
        raise ValueError("index 不能小于 0。")

    path = _artifact_directory(run_dir) / (
        f"{kind}_r{repair_round:02d}_s{stage_call:02d}_i{index:02d}.json"
    )
    envelope = {
        "stage": stage,
        "repair_round": repair_round,
        "stage_call": stage_call,
        "index": index,
        "payload": _json_payload(payload),
    }
    _write_json_exclusive(path, envelope)
    return path
