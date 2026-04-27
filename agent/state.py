"""
agent/state.py
--------------
LangGraph state definition for the StayEase booking agent.

Key LangGraph concept:
  - AgentState is a TypedDict passed through every node.
  - The `messages` field uses `Annotated[..., add_messages]` — this is the
    LangGraph reducer pattern. When a node returns {"messages": [new_msg]},
    LangGraph calls add_messages(existing, [new_msg]) to accumulate — nodes
    never need to manage the full list themselves.
  - All other fields are plain values; nodes return partial dicts and LangGraph
    does a shallow merge (last-write-wins per key).
"""

from __future__ import annotations

from typing import Any, Annotated
from typing_extensions import TypedDict

# LangGraph-native imports
from langgraph.graph.message import add_messages   # ← the reducer
from langchain_core.messages import BaseMessage    # shared message base type


class AgentState(TypedDict):
    """
    Single source of truth that flows through every node in the graph.

    LangGraph merges partial dicts returned by each node back into this state.
    The `add_messages` reducer on `messages` means nodes append — not replace.
    """

    # ── Session ───────────────────────────────────────────────────────────────
    conversation_id: str
    """Ties this run to a row in the conversations DB table."""

    # ── LLM message history (LangGraph-managed) ───────────────────────────────
    messages: Annotated[list[BaseMessage], add_messages]
    """
    Accumulated chat history: SystemMessage → HumanMessage(s) → AIMessage(s).

    Uses LangGraph's `add_messages` reducer so nodes just return the *new*
    message(s) they produce; LangGraph appends them automatically.

    Example — what a node should return:
        return {"messages": [AIMessage(content="Hello!")]}
        # NOT: return {"messages": state["messages"] + [AIMessage(...)]}
    """

    # ── Routing ───────────────────────────────────────────────────────────────
    intent: str
    """
    Intent classified by the LLM.
    One of: "search" | "details" | "book" | "escalate"
    Written by intent_router; read by the conditional edge in graph.py.
    """

    # ── Tool layer ────────────────────────────────────────────────────────────
    tool_input: dict[str, Any]
    """
    Structured parameters extracted from the guest message, ready to pass
    directly to the tool function.
    Example: {"location": "Cox's Bazar", "check_in": "2025-08-01",
               "check_out": "2025-08-03", "guests": 2}
    """

    tool_output: Any
    """
    Raw value returned by the tool.
    list[dict] for search, dict for details/book, None on error.
    """

    # ── Final output ──────────────────────────────────────────────────────────
    final_response: str
    """
    Human-readable reply sent back to the guest via FastAPI.
    Always populated before the graph reaches END.
    """

    # ── Error channel ─────────────────────────────────────────────────────────
    error: str | None
    """
    Set to an error message string when a node fails gracefully.
    response_node converts this into a polite apology for the guest.
    """
