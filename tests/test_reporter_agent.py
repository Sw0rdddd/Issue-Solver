from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from agents.reporter import build_report_agent
from prompts.reporter import REPORT_SYSTEM_PROMPT


def test_report_agent_returns_plain_text_without_tools() -> None:
    inputs: list[object] = []

    def invoke(value: object) -> AIMessage:
        inputs.append(value)
        return AIMessage(content="# 修复报告")

    agent = build_report_agent(RunnableLambda(invoke))

    result = agent.invoke([HumanMessage(content="最终状态")])

    assert result == "# 修复报告"
    assert len(inputs) == 1


def test_report_system_prompt_contains_the_only_output_template() -> None:
    assert "唯一输出模板" in REPORT_SYSTEM_PROMPT
    assert "禁止增加、删除、重命名或重排字段与章节" in REPORT_SYSTEM_PROMPT
    assert "# Issue 修复报告" in REPORT_SYSTEM_PROMPT
    assert "- 修改总结：" in REPORT_SYSTEM_PROMPT
    assert "- 验证总结：" in REPORT_SYSTEM_PROMPT
    assert "状态、Token、耗时和产物地址由程序" in REPORT_SYSTEM_PROMPT
    assert "- 修复轮次：" not in REPORT_SYSTEM_PROMPT
