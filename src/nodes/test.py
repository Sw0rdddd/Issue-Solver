from pathlib import Path

from config import Setting
from graph.state import ResolverState
from schemas.coding_task import CodingTask
from schemas.environment_info import EnvironmentInfo
from schemas.failure import failure_from_exception, make_failure
from schemas.test_result import TestResult
from services.artifacts import write_round_artifact
from services.test_executor import (
    append_safety_error,
    build_targeted_test_command,
    execute_test_command,
    worktree_fingerprint,
)


def build_test_node():
    """创建串行执行确定性测试命令的 LangGraph 节点。"""

    def test_node(state: ResolverState) -> dict:
        repair_round = state.get("repair_round", state.get("cycle", 0) + 1)
        run_dir = state.get("run_dir")
        results: list[TestResult] = []

        try:
            if repair_round < 1:
                raise RuntimeError("repair_round 必须大于 0。")
            repo_path = state.get("repo_path")
            base_commit = state.get("base_commit")
            if not repo_path:
                raise RuntimeError("State 中缺少 repo_path。")
            if not base_commit:
                raise RuntimeError("State 中缺少 base_commit。")
            if not run_dir:
                raise RuntimeError("State 中缺少 run_dir。")

            setting = Setting()
            timeout = state.get("test_timeout", setting.TEST_TIMEOUT)
            tail_lines = state.get("test_tail_lines", setting.TEST_TAIL_LINES)
            if timeout <= 0 or tail_lines < 1:
                raise RuntimeError("测试超时和日志尾部行数必须大于 0。")

            coding_task = state.get("coding_task")
            if not isinstance(coding_task, CodingTask):
                raise RuntimeError("State 中缺少有效的 CodingTask。")
            regression_commands = state.get("test_commands", [])
            if not regression_commands:
                raise RuntimeError("State 中缺少全量回归测试命令。")
            targeted_command = build_targeted_test_command(
                repo_path,
                coding_task.test_targets,
            )
            commands = list(
                dict.fromkeys([targeted_command, *regression_commands])
            )
            environment_value = state.get("environment")
            if environment_value is None:
                raise RuntimeError("State 中缺少已验证的目标虚拟环境。")
            environment = EnvironmentInfo.model_validate(environment_value)

            before = worktree_fingerprint(repo_path, base_commit)
            for index, command in enumerate(commands, start=1):
                result = execute_test_command(
                    repo_path=repo_path,
                    run_dir=run_dir,
                    command=command,
                    environment=environment,
                    timeout=timeout,
                    tail_lines=tail_lines,
                    repair_round=repair_round,
                    index=index,
                )
                results.append(result)
                if result.status != "PASSED":
                    break

            after = worktree_fingerprint(repo_path, base_commit)
            rollback_required = before != after
            if rollback_required:
                results[-1] = append_safety_error(
                    results[-1],
                    "测试执行修改了 Git 工作区。",
                    tail_lines,
                )

            write_round_artifact(
                run_dir=run_dir,
                kind="test_result",
                stage="TEST",
                repair_round=repair_round,
                payload=results,
            )
            update = {
                "test_results": results,
                "latest_test_results": results,
                "cycle": state.get("cycle", 0) + 1,
                "repair_round": repair_round,
                "phase": "COORDINATE",
            }
            if rollback_required:
                update.update(
                    {
                        "rollback_required": True,
                        "failure": make_failure(
                            "SAFETY",
                            "测试执行修改了 Git 工作区。",
                            "检查测试副作用；工作区必须回滚后才能重试。",
                        ),
                    }
                )
            elif any(result.status == "ENVIRONMENT_ERROR" for result in results):
                update.update(
                    {
                        "phase": "TEST",
                        "status": "FAILED",
                        "failure": make_failure(
                            "ENVIRONMENT",
                            "测试环境不可用。",
                            "请开发者修复目标仓库虚拟环境及依赖后重试。",
                        ),
                    }
                )
            return update

        except Exception as exc:
            failure = failure_from_exception(
                exc,
                "INTERNAL",
                prefix="Test 失败：",
            )
            if run_dir:
                try:
                    write_round_artifact(
                        run_dir=Path(run_dir),
                        kind="failure_test",
                        stage="TEST",
                        repair_round=max(repair_round, 1),
                        payload={
                            "failure": failure,
                            "results": results,
                        },
                    )
                except Exception as log_exc:
                    failure = make_failure(
                        "INTERNAL",
                        f"{failure.message}；记录失败产物失败：{log_exc}",
                    )
            return {
                "phase": "TEST",
                "status": "FAILED",
                "repair_round": max(repair_round, 1),
                "failure": failure,
            }

    return test_node
