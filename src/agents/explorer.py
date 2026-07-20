from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel

from prompts.explorer import EXPLORE_SYSTEM_PROMPT
from schemas.explore_report import ExploreReport
from tools.filesystem import list_files, read_file
from tools.git import git_log, git_show
from tools.search import search_symbol, search_text


def build_explore_agent(model: BaseChatModel):
    """创建只读的仓库探索 Agent。"""

    return create_agent(
        model=model,
        tools=[
            list_files,
            read_file,
            search_text,
            search_symbol,
            git_log,
            git_show,
        ],
        system_prompt=EXPLORE_SYSTEM_PROMPT,
        response_format=ToolStrategy(ExploreReport, handle_errors=True),
        name="explore_agent",
    )
