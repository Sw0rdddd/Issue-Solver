from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, tool

from agents.read_tools import build_bound_read_tools
from config import Setting
from prompts.reviewer import REVIEW_SYSTEM_PROMPT
from schemas.review_result import ReviewResult
from services.structured_output import with_agent_structured_output_retry
from services.tool_history import ToolHistoryWindowMiddleware
from tools.git import git_diff


def build_review_tools(repo_path: str, base_commit: str) -> list[BaseTool]:
    """创建绑定仓库根目录与基线 Commit 的 Review 只读工具。"""

    @tool("git_diff")
    def bound_git_diff(path: str = ".", max_chars: int = 20_000) -> str:
        """查看目标仓库当前代码相对固定基线 Commit 的差异。"""

        return git_diff.invoke(
            {
                "repo_path": repo_path,
                "base_commit": base_commit,
                "path": path,
                "max_chars": max_chars,
            }
        )

    return [*build_bound_read_tools(repo_path), bound_git_diff]


def build_review_agent(
    model: BaseChatModel,
    repo_path: str,
    base_commit: str,
):
    """创建只读检查当前代码修改的 Agent。"""

    agent = create_agent(
        model=model,
        tools=build_review_tools(repo_path, base_commit),
        system_prompt=REVIEW_SYSTEM_PROMPT,
        response_format=ToolStrategy(ReviewResult, handle_errors=True),
        middleware=[
            ToolHistoryWindowMiddleware(Setting().AGENT_RECURSION_LIMIT)
        ],
        name="review_agent",
    )
    return with_agent_structured_output_retry(
        agent,
        ReviewResult,
        agent_name="Review Agent",
    )
