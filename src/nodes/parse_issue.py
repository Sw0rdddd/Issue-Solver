from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from graph.state import ResolverState
from prompts.issue_parser import (
    ISSUE_PARSER_RECOVERY_PROMPT,
    ISSUE_PARSER_SYSTEM_PROMPT,
    build_issue_parser_input,
)
from schemas.failure import failure_from_exception, make_failure
from schemas.issue_specification import IssueSpec
from services.artifacts import ensure_run_logs_directory
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
            try:
                raw_issue = load_issue(state["issue_input"])
            except ValueError as exc:
                return {
                    "status": "FAILED",
                    "failure": failure_from_exception(
                        exc,
                        "INPUT",
                        prefix="Issue 加载失败：",
                    ),
                }
            except Exception as exc:
                return {
                    "status": "FAILED",
                    "failure": failure_from_exception(
                        exc,
                        "ENVIRONMENT",
                        prefix="Issue 加载失败：",
                    ),
                }

            # 2. 构造发送给模型的用户消息
            user_message = build_issue_parser_input(
                title=raw_issue.title,
                body=raw_issue.body,
                source=raw_issue.source,
            )

            # 3. 将原始 Issue 转换成 IssueSpec
            messages = [
                SystemMessage(content=ISSUE_PARSER_SYSTEM_PROMPT),
                HumanMessage(content=user_message),
            ]
            try:
                issue = structured_model.invoke(messages)
                acceptance_criteria = [
                    criterion.strip()
                    for criterion in issue.acceptance_criteria
                    if criterion.strip()
                ]
                if not acceptance_criteria:
                    recovered_issue = structured_model.invoke(
                        [
                            *messages,
                            HumanMessage(content=ISSUE_PARSER_RECOVERY_PROMPT),
                        ]
                    )
                    acceptance_criteria = [
                        criterion.strip()
                        for criterion in recovered_issue.acceptance_criteria
                        if criterion.strip()
                    ]
            except Exception as exc:
                return {
                    "status": "FAILED",
                    "failure": failure_from_exception(
                        exc,
                        "MODEL",
                        prefix="Issue 解析失败：",
                    ),
                }

            if not acceptance_criteria:
                return {
                    "status": "FAILED",
                    "failure": make_failure(
                        "INPUT",
                        "Issue 缺少可以安全确定的验收条件。",
                        "请补充明确的期望行为后重试。",
                    ),
                }
            issue = issue.model_copy(
                update={"acceptance_criteria": acceptance_criteria}
            )

            # 4. 保存规范化后的 Issue
            issue_path = (
                ensure_run_logs_directory(state["run_dir"]) / "issue.json"
            )

            issue_path.write_text(issue.model_dump_json(indent=2),encoding="utf-8")

            # 5. 返回对 State 的局部更新
            return {
                "issue": issue,
                "phase": "COORDINATE",
            }

        except Exception as exc:
            return {
                "status": "FAILED",
                "failure": failure_from_exception(
                    exc,
                    "INTERNAL",
                    prefix="Issue 解析失败：",
                ),
            }

    return parse_issue_node
