import json

from schemas.coding_result import CodingResult
from schemas.coding_task import CodingTask
from schemas.explore_report import ExploreReport
from schemas.issue_specification import IssueSpec


REVIEW_SYSTEM_PROMPT = """
你是 issue-solver 系统中的 Review Agent。

你的职责是只读检查 Coding Agent 产生的当前代码修改，判断修改是否正确解决 Issue，并返回结构化的 ReviewResult。

你必须遵守以下规则：

1. 你只能读取和分析仓库，禁止修改、创建或删除任何文件。
2. 必须先调用 git_diff，检查当前工作区相对于 base commit 的真实累计差异，不能只依据 CodingResult 的文字总结。
3. 如果 git_diff 输出被截断，应根据 CodingResult.changed_files 按文件再次调用 git_diff，不能在 Diff 不完整时直接批准。
4. 必须阅读修改后的相关文件及判断行为所需的上下文；只能根据工具返回的真实内容得出结论。
5. 审查修改是否对应根因、满足 Issue 和验收条件，并检查明显逻辑错误、边界情况、兼容性、回归风险、测试遗漏和修改范围。
6. 不得编造不存在的文件、行为或测试结果，也不得把未验证的推测写成确定事实。
7. 你不得执行测试，也不得声称测试已经通过；Review 结论不能替代后续 Test node。
8. issues 只记录会阻止当前修改通过的具体问题，并应指出相关文件或符号以及可能造成的后果。
9. suggestions 记录建议的修复方式或非阻断改进；纯代码风格偏好不能单独导致 REQUEST_CHANGES。
10. remaining_risks 记录当前证据仍无法排除但不阻断通过的风险。
11. 只有在发现影响验收条件、正确性、安全性、兼容性或必要测试覆盖的具体问题时，才能返回 REQUEST_CHANGES。
12. verdict 为 APPROVE 时 issues 必须为空；verdict 为 REQUEST_CHANGES 时 issues 必须至少包含一个具体问题。
13. 完成审查后立即返回完整的 ReviewResult，不要继续无意义搜索。
"""


def build_review_input(
    repo_path: str,
    base_commit: str,
    issue: IssueSpec,
    coding_task: CodingTask,
    coding_result: CodingResult,
    explore_reports: list[ExploreReport],
    current_summary: str = "",
) -> str:
    """构造 Review Agent 的审查消息。"""

    reports = [report.model_dump(mode="json") for report in explore_reports]
    return f"""
请审查当前代码修改。

仓库根目录：
{repo_path}

基础 Commit：
{base_commit}

当前工作流摘要：
{current_summary or "暂无"}

规范化 Issue：
{issue.model_dump_json(indent=2)}

CodingTask：
{coding_task.model_dump_json(indent=2)}

CodingResult：
{coding_result.model_dump_json(indent=2)}

Explore Reports：
{json.dumps(reports, ensure_ascii=False, indent=2) if reports else "暂无"}

执行要求：
1. 调用 git_diff 时，repo_path 和 base_commit 必须分别使用上述仓库根目录和基础 Commit。
2. list_files、read_file 和搜索工具的 repo_path 必须使用上述仓库根目录。
3. 先检查真实 Diff，再阅读修改后的相关文件和必要上下文。
4. 不执行测试，不修改文件，最终返回完整的 ReviewResult。
""".strip()
