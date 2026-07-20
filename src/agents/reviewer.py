from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel

from prompts.reviewer import REVIEW_SYSTEM_PROMPT
from schemas.review_result import ReviewResult
from services.structured_output import with_agent_structured_output_retry
from tools.filesystem import list_files, read_file
from tools.git import git_diff
from tools.search import search_symbol, search_text


def build_review_agent(model: BaseChatModel):
    """创建只读检查当前代码修改的 Agent。"""

    agent = create_agent(
        model=model,
        tools=[
            list_files,
            read_file,
            search_text,
            search_symbol,
            git_diff,
        ],
        system_prompt=REVIEW_SYSTEM_PROMPT,
        response_format=ToolStrategy(ReviewResult, handle_errors=True),
        name="review_agent",
    )
    return with_agent_structured_output_retry(
        agent,
        ReviewResult,
        agent_name="Review Agent",
    )
