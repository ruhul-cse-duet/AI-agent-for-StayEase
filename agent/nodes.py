"""
Node functions for the StayEase LangGraph agent.
"""

from __future__ import annotations

import json
import os
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.state import AgentState
from agent.tools import TOOL_MAP


_llm = None


def _get_llm():
    """Return a cached LLM instance, building it on first call."""
    global _llm
    if _llm is not None:
        return _llm

    base_url = os.getenv("LM_STUDIO_BASE_URL")
    if base_url:
        from langchain_openai import ChatOpenAI

        _llm = ChatOpenAI(
            base_url=base_url,
            api_key="lm-studio",
            model=os.getenv("LM_STUDIO_MODEL", "local-model"),
            temperature=0,
        )
    else:
        from langchain_groq import ChatGroq

        _llm = ChatGroq(
            api_key=os.environ["GROQ_API_KEY"],
            model=os.getenv("GROQ_MODEL", "llama3-70b-8192"),
            temperature=0,
        )
    return _llm


SYSTEM_PROMPT = """You are the StayEase booking assistant for a short-term rental platform in Bangladesh.
You ONLY handle three tasks:
  1. Search - find available properties given location, dates, and guest count.
  2. Details - return full information about a specific listing.
  3. Book - create a confirmed booking.

For every user message output STRICT JSON (no markdown) in this shape:
{
  "intent": "<search|details|book|escalate>",
  "tool_input": { ... }
}

- Dates must be in YYYY-MM-DD format.
- If the user asks about anything outside the three tasks, set intent to "escalate".
- Never make up listing IDs or prices.
- Prices are always in BDT.
"""


def input_node(state: AgentState) -> dict[str, Any]:
    msgs = state.get("messages", [])
    if not msgs:
        msgs = [SystemMessage(content=SYSTEM_PROMPT)]
    return {"messages": msgs, "error": None}


def intent_router(state: AgentState) -> dict[str, Any]:
    llm = _get_llm()
    response: AIMessage = llm.invoke(state["messages"])
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


def tool_executor(state: AgentState) -> dict[str, Any]:
    intent = state["intent"]
    tool_name_map = {
        "search": "search_available_properties",
        "details": "get_listing_details",
        "book": "create_booking",
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


def response_node(state: AgentState) -> dict[str, Any]:
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
            "Write a friendly reply listing each property with name, price in BDT, "
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

    llm = _get_llm()
    reply_msg: AIMessage = llm.invoke(
        [
            SystemMessage(content="You are StayEase assistant. Reply in clear, friendly English."),
            HumanMessage(content=user_prompt),
        ]
    )
    reply_text: str = reply_msg.content.strip()

    return {
        "final_response": reply_text,
        "messages": state["messages"] + [AIMessage(content=reply_text)],
    }


def escalation_node(state: AgentState) -> dict[str, Any]:
    escalation_msg = (
        "I'm sorry, I can only help with property searches, listing details, "
        "and bookings on StayEase. For anything else, please contact our support "
        "team at support@stayease.com.bd or call 16700."
    )
    return {"final_response": escalation_msg}

