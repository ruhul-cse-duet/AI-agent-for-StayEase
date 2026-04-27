"""
Node functions for the StayEase LangGraph agent.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.state import AgentState
from agent.tools import TOOL_MAP


_llm = None

_GREETING_WORDS = {
    "hi",
    "hello",
    "hey",
    "assalamu",
    "assalamualaikum",
    "salam",
    "goodmorning",
    "goodafternoon",
    "goodevening",
}

_PROPERTY_WORDS = {"hotel", "resort", "villa", "apartment", "property", "room", "rooms"}
_DETAIL_WORDS = {"detail", "details", "info", "information", "about", "describe", "describe", "know"}
_SEARCH_WORDS = {"search", "find", "available", "availability", "options", "list", "show"}
_BOOK_WORDS = {"book", "booking", "reserve", "reservation", "confirm"}

_KNOWN_LOCATIONS = {
    "sylhet": "Sylhet",
    "sajek": "Sajek",
    "dhaka": "Dhaka",
    "chittagong": "Chittagong",
    "ctg": "Chittagong",
}


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


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _tokenize(text: str) -> list[str]:
    normalized = re.sub(r"[^a-zA-Z0-9\s']", " ", text or "").lower()
    return [t for t in normalized.split() if t]


def _last_user_message(state: AgentState) -> str:
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            return _normalize_text(msg.content)
    return ""


def _is_greeting_only(text: str) -> bool:
    tokens = _tokenize(text)
    if not tokens:
        return False
    compact = "".join(tokens)
    return compact in _GREETING_WORDS or all(t in _GREETING_WORDS for t in tokens)


def _extract_date_pair(text: str) -> tuple[str | None, str | None]:
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", text or "")
    if len(dates) >= 2:
        return dates[0], dates[1]
    return None, None


def _extract_guests(text: str) -> int | None:
    m = re.search(r"\b(\d{1,2})\s*(?:guest|guests|people|person|persons)\b", text.lower())
    return int(m.group(1)) if m else None


def _extract_location_hint(text: str) -> str | None:
    tokens = _tokenize(text)
    if "cox" in tokens and "bazar" in tokens:
        return "Cox's Bazar"
    if "coxs" in tokens and "bazar" in tokens:
        return "Cox's Bazar"

    for token in tokens:
        if token in _KNOWN_LOCATIONS:
            return _KNOWN_LOCATIONS[token]
    return None


def _extract_listing_name(text: str) -> str | None:
    compact = _normalize_text(text)
    patterns = [
        r"(?:about|for|of)\s+([a-zA-Z0-9' -]{3,}(?:hotel|resort|villa|apartment)[a-zA-Z0-9' -]*)",
        r"(?:details\s+of|information\s+for)\s+([a-zA-Z0-9' -]{3,})",
    ]
    for p in patterns:
        m = re.search(p, compact, flags=re.IGNORECASE)
        if m:
            return _normalize_text(m.group(1).strip(" .,!?:;"))

    m = re.search(r"([a-zA-Z0-9' -]{3,}(?:hotel|resort|villa|apartment)[a-zA-Z0-9' -]*)", compact, flags=re.IGNORECASE)
    if m:
        return _normalize_text(m.group(1).strip(" .,!?:;"))
    return None


def _heuristic_intent_and_input(text: str) -> tuple[str, dict[str, Any]] | None:
    tokens = set(_tokenize(text))
    if not tokens:
        return None

    listing_id_match = re.search(r"\b(?:listing|property)\s*#?\s*(\d+)\b", text.lower())
    listing_name = _extract_listing_name(text)
    location = _extract_location_hint(text)
    check_in, check_out = _extract_date_pair(text)
    guests = _extract_guests(text)

    if listing_id_match:
        return "details", {"listing_id": int(listing_id_match.group(1))}

    if tokens & _BOOK_WORDS:
        tool_input: dict[str, Any] = {}
        if listing_name:
            tool_input["listing_name"] = listing_name
        if check_in and check_out:
            tool_input["check_in"] = check_in
            tool_input["check_out"] = check_out
        if guests:
            tool_input["guests"] = guests
        return "book", tool_input

    if (tokens & _DETAIL_WORDS) and listing_name:
        tool_input = {"listing_name": listing_name}
        if location:
            tool_input["location"] = location
        return "details", tool_input

    if (tokens & _SEARCH_WORDS) or (tokens & _PROPERTY_WORDS) or location:
        tool_input = {}
        if location:
            tool_input["location"] = location
        if check_in and check_out:
            tool_input["check_in"] = check_in
            tool_input["check_out"] = check_out
        if guests:
            tool_input["guests"] = guests
        return "search", tool_input

    return None


def _normalize_search_input(tool_input: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    normalized: dict[str, Any] = dict(tool_input or {})

    if "guests" not in normalized:
        for alias in ("guest_count", "guest", "people", "persons"):
            if alias in normalized and normalized[alias] not in ("", None):
                normalized["guests"] = normalized[alias]
                break

    if ("check_in" not in normalized or "check_out" not in normalized) and "dates" in normalized:
        check_in, check_out = _extract_date_pair(str(normalized.get("dates", "")))
        if check_in and check_out:
            normalized["check_in"] = check_in
            normalized["check_out"] = check_out

    if not normalized.get("location"):
        return {}, "Please share the location/city for your stay."
    if not normalized.get("check_in") or not normalized.get("check_out"):
        return {}, "Please share check-in and check-out dates in YYYY-MM-DD format."
    if not normalized.get("guests"):
        return {}, "Please share how many guests will stay."

    return normalized, None


SYSTEM_PROMPT = """You are the StayEase booking assistant for a short-term rental platform in Bangladesh.
You handle:
1) Search available properties (location + dates + guests)
2) Property details (by listing_id or listing_name)
3) Booking confirmation

Return STRICT JSON only:
{
  "intent": "<search|details|book|escalate>",
  "tool_input": { ... }
}

Rules:
- Dates: YYYY-MM-DD
- For details intent, prefer listing_id; if unavailable use listing_name
- If message is outside StayEase scope, use "escalate"
- Never invent listing IDs or prices
"""


def input_node(state: AgentState) -> dict[str, Any]:
    if not state.get("messages"):
        return {"messages": [SystemMessage(content=SYSTEM_PROMPT)], "error": None}
    return {"error": None}


def intent_router(state: AgentState) -> dict[str, Any]:
    user_text = _last_user_message(state)

    if _is_greeting_only(user_text):
        parsed = {"intent": "escalate", "tool_input": {}}
        return {
            "intent": "escalate",
            "tool_input": {},
            "messages": [AIMessage(content=json.dumps(parsed))],
        }

    heuristic = _heuristic_intent_and_input(user_text)
    if heuristic:
        intent, tool_input = heuristic
        return {
            "intent": intent,
            "tool_input": tool_input,
            "messages": [AIMessage(content=json.dumps({"intent": intent, "tool_input": tool_input}))],
        }

    llm = _get_llm()
    raw = str(llm.invoke(state["messages"]).content).strip()
    try:
        parsed: dict[str, Any] = json.loads(raw)
        intent = str(parsed.get("intent", "escalate"))
        tool_input = parsed.get("tool_input", {}) or {}
    except (json.JSONDecodeError, TypeError):
        intent = "escalate"
        tool_input = {}
        raw = json.dumps({"intent": intent, "tool_input": tool_input})

    return {
        "intent": intent,
        "tool_input": tool_input,
        "messages": [AIMessage(content=raw)],
    }


def tool_executor(state: AgentState) -> dict[str, Any]:
    intent = state.get("intent", "")
    tool_input = state.get("tool_input", {}) or {}

    if intent == "search":
        tool_name = "search_available_properties"
        normalized, input_error = _normalize_search_input(tool_input)
        if input_error:
            return {"tool_output": None, "error": input_error}
        tool_input = normalized

    elif intent == "details":
        if tool_input.get("listing_id"):
            tool_name = "get_listing_details"
        elif tool_input.get("listing_name"):
            tool_name = "get_listing_details_by_name"
        else:
            return {"tool_output": None, "error": "Please share the property name or listing ID for details."}

    elif intent == "book":
        tool_name = "create_booking"
        required = ("listing_id", "guest_name", "guest_phone", "check_in", "check_out", "guests")
        if any(not tool_input.get(k) for k in required):
            return {
                "tool_output": None,
                "error": "To confirm booking, please provide listing ID, guest name, phone, check-in, check-out, and guests.",
            }

    else:
        return {"tool_output": None, "error": f"No tool mapped for intent '{intent}'"}

    tool_fn = TOOL_MAP.get(tool_name)
    if not tool_fn:
        return {"tool_output": None, "error": f"Tool '{tool_name}' is not available."}

    try:
        return {"tool_output": tool_fn.invoke(tool_input), "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"tool_output": None, "error": str(exc)}


def response_node(state: AgentState) -> dict[str, Any]:
    if state.get("error"):
        return {"final_response": str(state["error"])}

    tool_output = state.get("tool_output")
    intent = state.get("intent", "unknown")

    if intent == "details" and not tool_output:
        return {"final_response": "I could not find that property. Please share the exact listing name or listing ID."}

    prompt_map = {
        "search": (
            "Guest searched properties. Data:\n"
            f"{json.dumps(tool_output, default=str, ensure_ascii=False)}\n\n"
            "Write concise, friendly results with name, nightly price in BDT, and 2 top amenities."
        ),
        "details": (
            "Guest requested listing details. Data:\n"
            f"{json.dumps(tool_output, default=str, ensure_ascii=False)}\n\n"
            "Write concise details with name, location, address, price in BDT, capacity, amenities, and host contact."
        ),
        "book": (
            "Booking confirmed. Data:\n"
            f"{json.dumps(tool_output, default=str, ensure_ascii=False)}\n\n"
            "Write concise booking confirmation with booking ID, property, dates, guests, and total BDT."
        ),
    }
    user_prompt = prompt_map.get(intent, f"Summarize for guest:\n{json.dumps(tool_output, default=str, ensure_ascii=False)}")

    llm = _get_llm()
    reply_text = str(
        llm.invoke(
            [
                SystemMessage(content="You are StayEase assistant. Reply in clear and natural English."),
                HumanMessage(content=user_prompt),
            ]
        ).content
    ).strip()
    return {"final_response": reply_text, "messages": [AIMessage(content=reply_text)]}


def escalation_node(state: AgentState) -> dict[str, Any]:
    user_text = _last_user_message(state)
    if _is_greeting_only(user_text):
        return {
            "final_response": (
                "Hello! Welcome to StayEase. I can help with property search, listing details, "
                "and bookings. Tell me your location, dates, and guests."
            )
        }

    return {
        "final_response": (
            "Hello! I can help with StayEase property search, listing details, and bookings. "
            "For other requests, please contact support@stayease.com.bd or 16700."
        )
    }

