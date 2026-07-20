from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel

from prompts.coder import CODING_SYSTEM_PROMPT
from schemas.coding_result import CodingResult
from tools.coding import CodingToolContext, build_coding_tools
from tools.filesystem import list_files, read_file
from tools.search import search_symbol, search_text


def build_coding_agent(
    model: BaseChatModel,
    context: CodingToolContext,
):
    """创建只能在绑定范围内读取、修改和检查代码的 Agent。"""

    apply_patch_tool, inspect_changes_tool = build_coding_tools(context)

    return create_agent(
        model=model,
        tools=[
            list_files,
            read_file,
            search_text,
            search_symbol,
            apply_patch_tool,
            inspect_changes_tool,
        ],
        system_prompt=CODING_SYSTEM_PROMPT,
        response_format=ToolStrategy(CodingResult, handle_errors=True),
        name="coding_agent",
    )
