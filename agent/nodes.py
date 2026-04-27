"""
agent/nodes.py
--------------
Five node functions that make up the StayEase LangGraph agent.
Each function receives the full AgentState, does one job, and returns
a partial dict that LangGraph merges back into state.
"""

from __future__ import annotations

import json
import os
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from agent.state import AgentState
from agent.tools import TOOL_MAP, search_available_properties  # noqa: F401


# ── LLM factory ──────────────────────────────────────────────────────────────
# Swap GROQ_API_KEY for LM_STUDIO_BASE_URL env var to use a local LM Studio model.

def _build_llm():
    """Return a ChatGroq instance (or OpenAI-compatible client for LM Studio)."""
    base_url = os.getenv("LM_STUDIO_BASE_URL")  # e.g. http://localhost:1234/v1
    if base_url:
        from langchain_openai import ChatOpenAI  # LM Studio uses OpenAI-compatible API
        return ChatOpenAI(
            base_url=base_url,
            api_key="lm-studio",       # LM Studio ignores the key value
            model=os.getenv("LM_STUDIO_MODEL", "local-model"),
            temperature=0,
        )
    return ChatGroq(
        api_key=os.environ["GROQ_API_KEY"],
        model=os.getenv("GROQ_MODEL", "llama3-70b-8192"),
        temperature=0,
    )


LLM = _build_llm()

# ── System prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the StayEase booking assistant for a short-term rental platform in Bangladesh.
You ONLY handle three tasks:
  1. Search — find available properties given location, dates, and guest count.
  2. Details — return full information about a specific listing.
  3. Book — create a confirmed booking.

For every user message output STRICT JSON (no markdown) in this shape:
{
  "intent": "<search|details|book|escalate>",
  "tool_input": { ... }   // parameters for the chosen tool; empty dict for escalate
}

- Dates must be in YYYY-MM-DD format.
- If the user asks about anything outside the three tasks, set intent to "escalate".
- Never make up listing IDs or prices.
- Prices are always in BDT (Bangladeshi Taka, ৳).
"""


# ── Node 1 — input_node ───────────────────────────────────────────────────────

def input_node(state: AgentState) -> dict[str, Any]:
    """
    Initialise or extend the messages list with the latest human turn.

    Reads `state["messages"]`; if it already contains history (multi-turn),
    just returns unchanged so the existing history is preserved.
    Adds the system prompt as the first message when the list is empty.
    """
    msgs = state.get("messages", [])
    if not msgs:
        msgs = [SystemMessage(content=SYSTEM_PROMPT)]
    return {"messages": msgs, "error": None}


# ── Node 2 — intent_router ────────────────────────────────────────────────────

def intent_router(state: AgentState) -> dict[str, Any]:
    """
    Call the LLM to classify the guest's latest message and extract tool params.

    Parses the LLM's JSON response into `intent` and `tool_input` fields.
    Falls back to intent="escalate" on any parse error.
    """
    response: AIMessage = LLM.invoke(state["messages"])
    raw = response.content.strip()

    try:
        parsed: dict = json.loads(raw)
        intent: str = parsed.get("intent", "escalate")
        tool_input: dict = parsed.get("tool_input", {})
    except (json.JSONDecodeError, AttributeError):
        intent = "escalate"
        tool_input = {}

    return {
        "intent": intent,
        "tool_input": tool_input,
        "messages": state["messages"] + [AIMessage(content=raw)],
    }


# ── Node 3 — tool_executor ────────────────────────────────────────────────────

def tool_executor(state: AgentState) -> dict[str, Any]:
    """
    Invoke the correct tool function based on `state["intent"]`.

    Looks up the tool in TOOL_MAP, calls it with `state["tool_input"]`,
    and stores the result in `state["tool_output"]`.
    Catches exceptions and stores the message in `state["error"]`.
    """
    intent = state["intent"]
    tool_name_map = {
        "search":  "search_available_properties",
        "details": "get_listing_details",
        "book":    "create_booking",
    }
    tool_name = tool_name_map.get(intent)
    if not tool_name or tool_name not in TOOL_MAP:
        return {"tool_output": None, "error": f"No tool mapped for intent '{intent}'"}

    tool_fn = TOOL_MAP[tool_name]
    try:
        result = tool_fn.invoke(state["tool_input"])
        return {"tool_output": result, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"tool_output": None, "error": str(exc)}


# ── Node 4 — response_node ────────────────────────────────────────────────────

def response_node(state: AgentState) -> dict[str, Any]:
    """
    Generate a friendly, BDT-priced reply from the tool's raw output.

    Builds a user-visible prompt that includes the tool result (or error),
    sends it to the LLM, and stores the plain-text reply in `final_response`.
    """
    if state.get("error"):
        reply = (
            f"Sorry, I ran into a problem: {state['error']}. "
            "Please try again or contact our support team."
        )
        return {"final_response": reply}

    tool_output = state.get("tool_output")
    intent = state.get("intent", "unknown")

    prompt_map = {
        "search": (
            "The guest searched for properties. Here are the results in JSON:\n"
            f"{json.dumps(tool_output, default=str, ensure_ascii=False)}\n\n"
            "Write a friendly reply listing each property with name, price in BDT (use ৳ symbol), "
            "and top 2 amenities. If the list is empty, apologise and suggest widening the search."
        ),
        "details": (
            "The guest asked for property details. Here is the listing data:\n"
            f"{json.dumps(tool_output, default=str, ensure_ascii=False)}\n\n"
            "Write a friendly summary covering: name, address, price per night in BDT, "
            "max guests, description, amenities, and host contact."
        ),
        "book": (
            "A booking was just confirmed. Here is the confirmation:\n"
            f"{json.dumps(tool_output, default=str, ensure_ascii=False)}\n\n"
            "Write a friendly confirmation message with booking ID, property name, dates, "
            "number of guests, and total cost in BDT."
        ),
    }

    user_prompt = prompt_map.get(
        intent,
        f"Tool result: {json.dumps(tool_output, default=str, ensure_ascii=False)}\nSummarise this for the guest.",
    )

    format_messages = [
        SystemMessage(content="You are StayEase assistant. Reply in clear, friendly English."),
        HumanMessage(content=user_prompt),
    ]
    reply_msg: AIMessage = LLM.invoke(format_messages)
    reply_text: str = reply_msg.content.strip()

    return {
        "final_response": reply_text,
        "messages": state["messages"] + [AIMessage(content=reply_text)],
    }


# ── Node 5 — escalation_node ──────────────────────────────────────────────────

def escalation_node(state: AgentState) -> dict[str, Any]:
    """
    Handle any intent the agent cannot process by handing off to a human.

    Sets a canned escalation message in `final_response` and does not call
    any tool.  The FastAPI layer can use this signal to notify an operator.
    """
    escalation_msg = (
        "I'm sorry, I can only help with property searches, listing details, "
        "and bookings on StayEase. For anything else, please contact our support "
        "team at support@stayease.com.bd or call 16700."
    )
    return {"final_response": escalation_msg}
