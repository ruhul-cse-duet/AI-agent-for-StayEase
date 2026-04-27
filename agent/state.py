"""
agent/state.py
--------------
Defines the single source of truth that flows through every LangGraph node.
"""

from __future__ import annotations

from typing import Any
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    """
    Shared state object passed between every node in the StayEase agent graph.

    All fields are optional-by-default so nodes only update what they own;
    LangGraph merges partial dicts on each transition.
    """

    # ── Session ───────────────────────────────────────────────────────────────
    conversation_id: str
    """Client-supplied ID that links this run to the conversations DB row."""

    # ── LLM context ───────────────────────────────────────────────────────────
    messages: list[BaseMessage]
    """
    Full chat history (HumanMessage / AIMessage / ToolMessage).
    Fed to the LLM on every node that needs conversational context.
    """

    # ── Routing ───────────────────────────────────────────────────────────────
    intent: str
    """
    Classified intent.
    One of: "search" | "details" | "book" | "escalate" | "unknown"
    Set by intent_router; read by the conditional edge.
    """

    # ── Tool layer ────────────────────────────────────────────────────────────
    tool_input: dict[str, Any]
    """
    Structured parameters extracted from the guest message.
    Passed directly into the tool function by tool_executor.
    Example: {"location": "Cox's Bazar", "check_in": "2025-08-01",
               "check_out": "2025-08-03", "guests": 2}
    """

    tool_output: Any
    """
    Raw value returned by the tool (list[dict] for search, dict for details/book).
    Consumed by response_node to generate the guest-facing reply.
    """

    # ── Output ────────────────────────────────────────────────────────────────
    final_response: str
    """
    Human-readable reply to be returned to the guest via FastAPI.
    Always written before the graph reaches END.
    """

    # ── Error handling ────────────────────────────────────────────────────────
    error: str | None
    """
    Non-None when a recoverable error occurred (e.g. DB timeout, LLM parse fail).
    response_node converts this into a polite apology message.
    """
