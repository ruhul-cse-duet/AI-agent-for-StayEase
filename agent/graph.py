"""
agent/graph.py  (v3 — final)
"""
from __future__ import annotations
from langgraph.graph import END, StateGraph
from agent.state import AgentState
from agent.nodes import (
    agent_node, input_node, response_node,
    should_continue, tool_node, _is_small_model,
)

def _after_tool(state: AgentState) -> str:
    """
    After tool_node:
    - Small model: tool_node already set final_response → go to response_node
    - Large model: loop back to agent_node so LLM can read tool result
    """
    if _is_small_model() or state.get("final_response"):
        return "response_node"
    return "agent_node"

def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("input_node",    input_node)
    graph.add_node("agent_node",    agent_node)
    graph.add_node("tool_node",     tool_node)
    graph.add_node("response_node", response_node)

    graph.set_entry_point("input_node")
    graph.add_edge("input_node",    "agent_node")
    graph.add_edge("response_node", END)

    # After agent_node: tool call? → tool_node  else → response_node
    graph.add_conditional_edges(
        "agent_node",
        should_continue,
        {"tool_node": "tool_node", "response_node": "response_node"},
    )

    # After tool_node: small model → response_node | large model → agent_node
    graph.add_conditional_edges(
        "tool_node",
        _after_tool,
        {"response_node": "response_node", "agent_node": "agent_node"},
    )

    return graph.compile()

agent_graph = build_graph()

def run_agent(initial_state: AgentState) -> AgentState:
    return agent_graph.invoke(initial_state)
