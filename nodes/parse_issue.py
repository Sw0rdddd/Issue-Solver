from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from graph.state import ResolverState
from prompts.issue_parser import (
    ISSUE_PARSER_SYSTEM_PROMPT,
    build_issue_parser_input,
)
from schemas.issue_specification import IssueSpec
from services.issue_loader import load_issue
from services.structured_output import with_structured_output_retry


def build_parse_issue_node(model: BaseChatModel):
    """创建 Parse Issue 节点。"""

    structured_model = with_structured_output_retry(
        model.with_structured_output(
            IssueSpec,
            method="function_calling",
            strict=None,
        )
    )

    def parse_issue_node(state: ResolverState) -> dict:
        """加载并规范化用户输入的 Issue。"""

        try:
            # 1. 加载文本或 GitHub Issue
            raw_issue = load_issue(state["issue_input"])

            # 2. 构造发送给模型的用户消息
            user_message = build_issue_parser_input(
                title=raw_issue.title,
                body=raw_issue.body,
                source=raw_issue.source,
            )

            # 3. 将原始 Issue 转换成 IssueSpec
            issue = structured_model.invoke([
                    SystemMessage(content=ISSUE_PARSER_SYSTEM_PROMPT),
                    HumanMessage(content=user_message),
                ])

            # 4. 保存规范化后的 Issue
            issue_path = Path(state["run_dir"]) / "issue.json"

            issue_path.write_text(issue.model_dump_json(indent=2),encoding="utf-8")

            # 5. 返回对 State 的局部更新
            return {
                "issue": issue,
                "phase": "COORDINATE",
            }

        except Exception as exc:
            return {
                "status": "FAILED",
                "error": f"Issue 解析失败：{exc}",
            }

    return parse_issue_node
