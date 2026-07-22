from typing import Literal

from langgraph.types import Send

from graph.state import NextAction, ResolverState


StepRoute = Literal["CONTINUE", "FAILED"]
TestRoute = Literal["COORDINATOR", "FINALIZE", "FAILED"]


def route_after_step(state: ResolverState) -> StepRoute:
    """普通节点失败时终止，否则进入下一固定节点。"""

    if state.get("status") == "FAILED":
        return "FAILED"
    return "CONTINUE"


def route_after_test(state: ResolverState) -> TestRoute:
    """测试通过并获 Review 批准时直接收尾，否则交回 Coordinator。"""

    if state.get("status") == "FAILED":
        return "FAILED"
    if state.get("next_action") == "FINISH":
        return "FINALIZE"
    return "COORDINATOR"


def route_after_coordinator(
    state: ResolverState,
) -> NextAction | list[Send]:
    """按 Coordinator 决策结束流程或动态派发 Explore。"""

    if state.get("status") == "FAILED":
        return "FAILED"

    next_action = state.get("next_action")
    if next_action == "EXPLORE":
        focuses = state.get("explore_focuses", [])
        titles = state.get("explore_titles", [])
        if not 1 <= len(focuses) <= 3:
            return "FAILED"
        if len(titles) != len(focuses):
            return "FAILED"

        repair_round = state.get("repair_round", state.get("cycle", 0) + 1)
        stage_call = state.get("explore_stage_call", 1)
        return [
            Send(
                "explore",
                {
                    **state,
                    "explore_focus": focus,
                    "explore_title": titles[index],
                    "repair_round": repair_round,
                    "explore_stage_call": stage_call,
                    "explore_item_index": index + 1,
                },
            )
            for index, focus in enumerate(focuses)
        ]

    if next_action in {"CODE", "FINISH", "FAILED"}:
        return next_action
    return "FAILED"
