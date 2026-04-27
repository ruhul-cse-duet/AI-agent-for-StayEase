"""
agent/state.py  (v4 — customer_id based)
-----------------------------------------
"""
from __future__ import annotations

from typing import Any, Annotated
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    """
    Shared state flowing through every node in the StayEase agent graph.
    """

    # ── Identity ───────────────────────────────────────────────────────────────
    customer_id: str
    """
    Unique ID for the guest using this chat session.
    e.g. "cust_abc123", "+8801888410789", or any string the frontend sends.
    This is the path parameter in /api/chat/{customer_id}/message.
    """

    conversation_id: int
    """
    Auto-increment PK of the current row in the conversations table.
    Resolved by main.py from customer_id on each request.
    """

    # ── LLM message history (LangGraph-managed) ───────────────────────────────
    messages: Annotated[list[BaseMessage], add_messages]

    # ── Multi-turn booking accumulator ────────────────────────────────────────
    pending_booking: dict[str, Any]
    """
    Partial booking info collected across turns. Cleared after successful booking.
    Keys: location, check_in, check_out, guests,
          listing_id, listing_name, guest_name, guest_phone
    """

    # ── Routing / logging ─────────────────────────────────────────────────────
    intent: str
    """One of: "search" | "details" | "book" | "escalate" | "" """

    # ── Tool layer ────────────────────────────────────────────────────────────
    tool_input: dict[str, Any]
    tool_output: str

    # ── Final output ──────────────────────────────────────────────────────────
    final_response: str

    # ── Error channel ─────────────────────────────────────────────────────────
    error: str | None
