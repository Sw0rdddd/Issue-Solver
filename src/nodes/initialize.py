from pathlib import Path

from graph.state import ResolverState
from schemas.failure import ClassifiedFailure, failure_from_exception, make_failure
from schemas.environment_info import EnvironmentInfo
from services.project_detector import (
    detect_project_type,
    detect_test_commands,
)
from services.repository import (
    find_repo_root,
    get_current_commit,
    get_repository_profile,
    is_worktree_clean,
)
from services.python_environment import discover_python_environment


def initialize_node(state: ResolverState) -> dict:
    """初始化 Git 仓库和项目环境。"""

    try:
        # 用户可能传入仓库中的某个子目录，
        # 因此先找到真正的 Git 仓库根目录。
        repo_root = find_repo_root(Path(state["repo_path"]))

        environment_value = state.get("environment")
        if environment_value is None:
            environment = discover_python_environment(
                repo_root,
                state["run_dir"],
            )
        else:
            environment = EnvironmentInfo.model_validate(environment_value)

        # 防止覆盖用户已有的代码修改。
        if not is_worktree_clean(repo_root):
            raise ClassifiedFailure(
                make_failure(
                    "SAFETY",
                    "Git 工作区存在未提交修改。",
                    "请先提交或清理现有修改后再运行。",
                )
            )

        # 记录任务开始时的代码版本。
        base_commit = get_current_commit(repo_root)

        # 第一版主要识别 Python 项目。
        project_type = detect_project_type(repo_root)

        if project_type == "unknown":
            raise RuntimeError("无法识别项目类型，第一版仅支持 Python 项目。")

        repository_profile = get_repository_profile(repo_root)

        # 识别基础测试命令，例如 pytest -q。
        test_commands = detect_test_commands(repo_root)
        if not test_commands:
            raise RuntimeError("未检测到可执行的 pytest 测试命令。")

        return {
            "repo_path": str(repo_root),
            "base_commit": base_commit,
            "project_type": project_type,
            "test_commands": test_commands,
            "environment": environment,
            "repository_profile": repository_profile,
            "phase": "PARSE_ISSUE",
        }

    except Exception as exc:
        return {
            "status": "FAILED",
            "failure": failure_from_exception(exc, "ENVIRONMENT"),
        }
