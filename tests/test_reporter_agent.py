from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from agents.reporter import build_report_agent


def test_report_agent_returns_plain_text_without_tools() -> None:
    inputs: list[object] = []

    def invoke(value: object) -> AIMessage:
        inputs.append(value)
        return AIMessage(content="# 修复报告")

    agent = build_report_agent(RunnableLambda(invoke))

    result = agent.invoke([HumanMessage(content="最终状态")])

    assert result == "# 修复报告"
    assert len(inputs) == 1
