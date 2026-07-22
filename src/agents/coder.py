from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from agents.read_tools import build_bound_read_tools
from prompts.coder import CODING_SYSTEM_PROMPT
from schemas.coding_result import CodingResult
from tools.coding import CodingToolContext, build_coding_tools


def build_coding_read_tools(context: CodingToolContext) -> list[BaseTool]:
    """创建已绑定仓库根目录的只读工具，避免模型传入错误根路径。"""

    return build_bound_read_tools(str(context.repo_root))


def build_coding_agent(
    model: BaseChatModel,
    context: CodingToolContext,
):
    """创建只能在绑定范围内读取、修改和检查代码的 Agent。"""

    apply_patch_tool, inspect_changes_tool = build_coding_tools(context)
    read_tools = build_coding_read_tools(context)

    return create_agent(
        model=model,
        tools=[
            *read_tools,
            apply_patch_tool,
            inspect_changes_tool,
        ],
        system_prompt=CODING_SYSTEM_PROMPT,
        response_format=ToolStrategy(CodingResult, handle_errors=True),
        name="coding_agent",
    )
