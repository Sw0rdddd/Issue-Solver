from pathlib import Path

from graph.state import ResolverState
from services.artifacts import write_round_artifact
from tools.coding import CodingToolContext, rollback_to_base, save_final_patch


def _context_from_state(state: ResolverState) -> CodingToolContext:
    repo_path = state.get("repo_path")
    base_commit = state.get("base_commit")
    run_dir = state.get("run_dir")
    changed_files = state.get("changed_files", [])
    if not repo_path or not base_commit or not run_dir:
        raise RuntimeError("State 缺少 Finalize 所需的仓库上下文。")
    if not changed_files:
        raise RuntimeError("State 中没有可收尾的 changed_files。")
    return CodingToolContext.create(
        repo_root=repo_path,
        base_commit=base_commit,
        run_dir=run_dir,
        allowed_paths=changed_files,
        repair_round=state.get("repair_round", state.get("cycle", 1)),
        stage_call=max(state.get("coding_stage_call", 1), 1),
        allow_existing_changes=True,
    )


def rollback_state_to_base(state: ResolverState, reason: str) -> dict:
    """按 State 中的受控上下文回滚当前累计修改。"""

    context = _context_from_state(state)
    return rollback_to_base(context, reason)


def build_finalize_node():
    """创建保存最终 Patch 或执行受控回滚的收尾节点。"""

    def finalize_node(state: ResolverState) -> dict:
        repair_round = state.get("repair_round", max(state.get("cycle", 1), 1))
        run_dir = state.get("run_dir")
        outcome: dict = {
            "next_action": state.get("next_action"),
            "rollback_required": state.get("rollback_required", False),
        }

        try:
            if state.get("next_action") == "FINISH":
                review_result = state.get("review_result")
                test_results = state.get("latest_test_results", [])
                context = _context_from_state(state)
                saved = save_final_patch(
                    context,
                    review_approved=(
                        review_result is not None
                        and review_result.verdict == "APPROVE"
                    ),
                    tests_passed=(
                        bool(test_results)
                        and all(result.status == "PASSED" for result in test_results)
                    ),
                )
                if not saved["success"]:
                    raise RuntimeError(saved["error"] or "最终 Patch 保存失败。")
                outcome.update({"status": "FINISHED", **saved["data"]})
                write_round_artifact(
                    run_dir=run_dir,
                    kind="finalize_result",
                    stage="FINALIZE",
                    repair_round=repair_round,
                    payload=outcome,
                )
                return {
                    "phase": "FINALIZE",
                    "status": "FINISHED",
                    "diff_path": saved["data"]["patch_path"],
                }

            if state.get("rollback_required"):
                reason = state.get("rollback_reason", state.get("error", "运行失败。"))
                rolled_back = rollback_state_to_base(state, reason)
                if not rolled_back["success"]:
                    raise RuntimeError(rolled_back["error"] or "回滚失败。")
                outcome.update(
                    {
                        "status": "FAILED",
                        "rollback_success": True,
                        **rolled_back["data"],
                    }
                )
                changed_files: list[str] = []
            else:
                outcome.update(
                    {
                        "status": "FAILED",
                        "rollback_success": False,
                        "reason": state.get("error", "运行失败。"),
                    }
                )
                changed_files = state.get("changed_files", [])

            if run_dir:
                write_round_artifact(
                    run_dir=Path(run_dir),
                    kind="finalize_result",
                    stage="FINALIZE",
                    repair_round=max(repair_round, 1),
                    payload=outcome,
                )
            return {
                "phase": "FINALIZE",
                "status": "FAILED",
                "changed_files": changed_files,
            }

        except Exception as exc:
            error = f"Finalize 失败：{exc}"
            return {
                "phase": "FINALIZE",
                "status": "FAILED",
                "error": error,
            }

    return finalize_node
