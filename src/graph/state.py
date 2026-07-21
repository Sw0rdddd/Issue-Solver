from operator import add
from typing import Annotated, Literal, NotRequired, TypedDict

from schemas.coding_result import CodingResult
from schemas.coding_task import CodingTask
from schemas.environment_info import EnvironmentInfo
from schemas.explore_report import ExploreReport
from schemas.failure import FailureInfo
from schemas.issue_specification import IssueSpec
from schemas.review_result import ReviewResult
from schemas.test_result import TestResult


Phase = Literal[
    "INITIALIZE",
    "PARSE_ISSUE",
    "COORDINATE",
    "EXPLORE",
    "CODE",
    "REVIEW",
    "TEST",
    "FINALIZE",
]

RunStatus = Literal[
    "RUNNING",
    "FINISHED",
    "FAILED",
]

NextAction = Literal[
    "EXPLORE",
    "CODE",
    "FINISH",
    "FAILED",
]


class ResolverState(TypedDict):
    """issue-solver 工作流中所有节点共享的状态。"""

    # 启动 StateGraph 前必须存在
    run_id: str
    phase: Phase
    status: RunStatus
    cycle: int
    repo_path: str
    run_dir: str #本次 Issue 修复任务的运行记录目录
    issue_input: str  # 用户输入的 Issue URL 或 Issue 文本

    # 运行级配置，未提供时由节点使用默认值
    max_cycles: NotRequired[int]
    agent_recursion_limit: NotRequired[int]
    max_explore_batches: NotRequired[int]
    test_timeout: NotRequired[float]
    test_tail_lines: NotRequired[int]

    # Initialize 节点写入
    base_commit: NotRequired[str]
    project_type: NotRequired[str]
    test_commands: NotRequired[list[str]]
    environment: NotRequired[EnvironmentInfo]

    # Parse Issue 节点写入
    issue: NotRequired[IssueSpec]

    # Coordinator 节点写入
    current_summary: NotRequired[str]
    next_action: NotRequired[NextAction]
    explore_focuses: NotRequired[list[str]]
    repair_round: NotRequired[int]
    explore_stage_call: NotRequired[int]
    coding_stage_call: NotRequired[int]
    coding_task: NotRequired[CodingTask]

    # Send 分支输入
    explore_focus: NotRequired[str]
    explore_item_index: NotRequired[int]

    # Explore 节点并行写入
    explore_reports: NotRequired[
        Annotated[list[ExploreReport], add]
    ]
    explore_failures: NotRequired[Annotated[list[FailureInfo], add]]

    # Coding 节点写入
    coding_result: NotRequired[CodingResult]
    changed_files: NotRequired[list[str]]
    coding_iteration: NotRequired[int]
    diff_path: NotRequired[str]

    # Review 节点写入
    review_result: NotRequired[ReviewResult]

    # Test 节点写入
    test_results: NotRequired[Annotated[list[TestResult], add]]
    latest_test_results: NotRequired[list[TestResult]]

    # 失败收尾策略
    rollback_required: NotRequired[bool]
    rollback_success: NotRequired[bool]
    rollback_failure: NotRequired[FailureInfo]

    # 节点执行失败时写入
    failure: NotRequired[FailureInfo]
