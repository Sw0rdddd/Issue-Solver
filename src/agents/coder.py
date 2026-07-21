from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool

from prompts.coder import CODING_SYSTEM_PROMPT
from schemas.coding_result import CodingResult
from tools.coding import CodingToolContext, build_coding_tools
from tools.filesystem import list_files, read_file
from tools.search import search_symbol, search_text


def build_coding_read_tools(context: CodingToolContext) -> list[object]:
    """创建已绑定仓库根目录的只读工具，避免模型传入错误根路径。"""

    repo_path = str(context.repo_root)

    @tool("list_files")
    def bound_list_files(
        path: str = ".",
        max_depth: int = 1,
        max_entries: int = 500,
    ) -> str:
        """列出目标仓库内指定相对目录的文件和子目录。"""

        return list_files.invoke(
            {
                "repo_path": repo_path,
                "path": path,
                "max_depth": max_depth,
                "max_entries": max_entries,
            }
        )

    @tool("read_file")
    def bound_read_file(
        path: str,
        start_line: int = 1,
        end_line: int = 200,
    ) -> str:
        """读取目标仓库内指定相对文件的部分内容。"""

        return read_file.invoke(
            {
                "repo_path": repo_path,
                "path": path,
                "start_line": start_line,
                "end_line": end_line,
            }
        )

    @tool("search_text")
    def bound_search_text(
        query: str,
        path: str = ".",
        file_pattern: str = "*",
        case_sensitive: bool = False,
        max_results: int = 50,
    ) -> str:
        """在目标仓库相对路径内搜索文本。"""

        return search_text.invoke(
            {
                "repo_path": repo_path,
                "query": query,
                "path": path,
                "file_pattern": file_pattern,
                "case_sensitive": case_sensitive,
                "max_results": max_results,
            }
        )

    @tool("search_symbol")
    def bound_search_symbol(symbol: str, path: str = ".") -> str:
        """在目标仓库相对路径内搜索 Python 符号。"""

        return search_symbol.invoke(
            {
                "repo_path": repo_path,
                "symbol": symbol,
                "path": path,
            }
        )

    return [
        bound_list_files,
        bound_read_file,
        bound_search_text,
        bound_search_symbol,
    ]


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
