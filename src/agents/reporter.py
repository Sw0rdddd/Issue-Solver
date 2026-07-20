from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser


def build_report_agent(model: BaseChatModel):
    """创建不绑定任何工具、只返回 Markdown 文本的 Reporter。"""

    return model | StrOutputParser()
