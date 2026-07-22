from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, tool

from agents.read_tools import build_bound_read_tools
from config import Setting
from prompts.explorer import EXPLORE_SYSTEM_PROMPT
from schemas.explore_report import ExploreReport
from services.structured_output import with_agent_structured_output_retry
from services.tool_history import ToolHistoryWindowMiddleware
from tools.git import git_log, git_show


def build_explore_tools(repo_path: str) -> list[BaseTool]:
    """创建绑定仓库根目录的 Explore 只读工具。"""

    @tool("git_log")
    def bound_git_log(path: str = ".", limit: int = 10) -> str:
        """查看目标仓库指定相对路径相关的最近 Git 提交记录。"""

        return git_log.invoke(
            {
                "repo_path": repo_path,
                "path": path,
                "limit": limit,
            }
        )

    @tool("git_show")
    def bound_git_show(
        commit: str,
        path: str = ".",
        max_chars: int = 20_000,
    ) -> str:
        """查看指定 Commit 对目标仓库相对路径所做的修改。"""

        return git_show.invoke(
            {
                "repo_path": repo_path,
                "commit": commit,
                "path": path,
                "max_chars": max_chars,
            }
        )

    return [*build_bound_read_tools(repo_path), bound_git_log, bound_git_show]


def build_explore_agent(model: BaseChatModel, repo_path: str):
    """创建只读的仓库探索 Agent。"""

    agent = create_agent(
        model=model,
        tools=build_explore_tools(repo_path),
        system_prompt=EXPLORE_SYSTEM_PROMPT,
        response_format=ToolStrategy(ExploreReport, handle_errors=True),
        middleware=[
            ToolHistoryWindowMiddleware(Setting().AGENT_RECURSION_LIMIT)
        ],
        name="explore_agent",
    )
    return with_agent_structured_output_retry(
        agent,
        ExploreReport,
        agent_name="Explore Agent",
    )
