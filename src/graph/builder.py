from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from agents.coordinator import build_coordinator_agent
from agents.explorer import build_explore_agent
from agents.reviewer import build_review_agent
from graph.routing import (
    route_after_coordinator,
    route_after_step,
    route_after_test,
)
from graph.state import ResolverState
from nodes.coding import build_coding_node
from nodes.coordinator import build_coordinator_node
from nodes.explore import build_explore_node
from nodes.finalize import build_finalize_node
from nodes.initialize import initialize_node
from nodes.parse_issue import build_parse_issue_node
from nodes.review import build_review_node
from nodes.test import build_test_node
from services.openai_compatible_model import build_non_thinking_model


def build_graph(model: BaseChatModel) -> StateGraph:
    """创建并注册当前已实现节点的 StateGraph Builder。"""

    builder = StateGraph(ResolverState)
    non_thinking_model = build_non_thinking_model(model)

    parse_issue_node = build_parse_issue_node(non_thinking_model)
    coordinator_agent = build_coordinator_agent(model)
    coordinator_node = build_coordinator_node(coordinator_agent)
    explore_node = build_explore_node(
        lambda repo_path: build_explore_agent(
            non_thinking_model,
            repo_path,
        )
    )
    coding_node = build_coding_node(model)
    review_node = build_review_node(
        lambda repo_path, base_commit: build_review_agent(
            model,
            repo_path,
            base_commit,
        )
    )
    test_node = build_test_node()
    finalize_node = build_finalize_node()

    builder.add_node("initialize", initialize_node)
    builder.add_node("parse_issue", parse_issue_node)
    builder.add_node("coordinator", coordinator_node)
    builder.add_node("explore", explore_node)
    builder.add_node("coding", coding_node)
    builder.add_node("review", review_node)
    builder.add_node("test", test_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "initialize")
    builder.add_conditional_edges(
        "initialize",
        route_after_step,
        {
            "CONTINUE": "parse_issue",
            "FAILED": END,
        },
    )
    builder.add_conditional_edges(
        "parse_issue",
        route_after_step,
        {
            "CONTINUE": "coordinator",
            "FAILED": END,
        },
    )
    builder.add_conditional_edges(
        "coordinator",
        route_after_coordinator,
        {
            "CODE": "coding",
            "FINISH": "finalize",
            "FAILED": "finalize",
        },
    )
    builder.add_edge("explore", "coordinator")
    builder.add_conditional_edges(
        "coding",
        route_after_step,
        {
            "CONTINUE": "review",
            "FAILED": END,
        },
    )
    builder.add_conditional_edges(
        "review",
        route_after_step,
        {
            "CONTINUE": "test",
            "FAILED": END,
        },
    )
    builder.add_conditional_edges(
        "test",
        route_after_test,
        {
            "COORDINATOR": "coordinator",
            "FINALIZE": "finalize",
            "FAILED": END,
        },
    )
    builder.add_edge("finalize", END)

    return builder
