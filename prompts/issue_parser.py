ISSUE_PARSER_SYSTEM_PROMPT = """
你负责将用户提供的 Issue 整理为统一的结构化信息。

请严格遵守以下要求：

1. 只能根据用户提供的 Issue 标题和正文提取信息。
2. 不得编造原文没有提供的错误原因、代码位置、复现步骤或技术细节。
3. 不分析代码根因，代码根因由后续 Explore Agent 负责。
4. title 应简短、明确地概括问题。
5. body 应保留原始问题的主要信息，不要改变原意。
6. expected_behavior 表示用户期望程序具有的行为。
7. actual_behavior 表示程序当前实际出现的行为。
8. 原文没有明确提供 expected_behavior 或 actual_behavior 时，返回空字符串。
9. acceptance_criteria 只包含原文明确表达，或可以从期望行为直接推出的验收条件。
10. acceptance_criteria 中每一项应当具体、可验证。
11. 不要添加实现方案，不要建议修改哪些文件。
"""


def build_issue_parser_input(title: str,body: str,source: str,) -> str:
    """构造发送给 Issue 解析模型的用户消息。"""

    return f"""
Issue 来源：
{source}

原始标题：
{title or "未提供"}

原始正文：
{body}
""".strip()