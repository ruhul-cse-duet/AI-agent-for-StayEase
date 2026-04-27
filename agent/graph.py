"""
agent/graph.py
--------------
Assembles the LangGraph StateGraph for the StayEase booking agent.

Graph structure
---------------
    input_node
        │
    intent_router   ──── (conditional) ────┐
        │                                  │
    tool_executor                   escalation_node
        │                                  │
    response_node ─────────────────────────┘
        │
       END
"""

from __future__ import annotations

from typing import Literal

from langgraph.graph import END, StateGraph

from agent.state import AgentState
from agent.nodes import (
    escalation_node,
    input_node,
    intent_router,
    response_node,
    tool_executor,
)


# ── Routing function (conditional edge) ──────────────────────────────────────

def route_by_intent(
    state: AgentState,
) -> Literal["tool_executor", "escalation_node"]:
    """
    Read `state["intent"]` and return the name of the next node.

    - Actionable intents (search / details / book) → tool_executor
    - Anything else (escalate / unknown) → escalation_node
    """
    if state.get("intent") in {"search", "details", "book"}:
        return "tool_executor"
    return "escalation_node"


# ── Graph construction ────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """
    Build and compile the StayEase LangGraph agent.

    Returns a compiled graph ready to be invoked with an initial AgentState.
    """
    graph = StateGraph(AgentState)

    # Register nodes
    graph.add_node("input_node",      input_node)
    graph.add_node("intent_router",   intent_router)
    graph.add_node("tool_executor",   tool_executor)
    graph.add_node("response_node",   response_node)
    graph.add_node("escalation_node", escalation_node)

    # Entry point
    graph.set_entry_point("input_node")

    # Linear edges
    graph.add_edge("input_node",    "intent_router")
    graph.add_edge("tool_executor", "response_node")
    graph.add_edge("response_node", END)
    graph.add_edge("escalation_node", END)

    # Conditional edge — branches after intent classification
    graph.add_conditional_edges(
        "intent_router",
        route_by_intent,
        {
            "tool_executor":   "tool_executor",
            "escalation_node": "escalation_node",
        },
    )

    return graph.compile()


# ── Singleton ─────────────────────────────────────────────────────────────────

agent_graph = build_graph()


def run_agent(initial_state: AgentState) -> AgentState:
    """
    Execute the graph from *initial_state* and return the final state.

    FastAPI calls this function once per incoming message.
    """
    return agent_graph.invoke(initial_state)
