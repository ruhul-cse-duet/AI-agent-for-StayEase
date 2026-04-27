"""
agent/nodes.py  (v5 — full multi-turn booking flow)
-----------------------------------------------------
Enforces a 4-step guest journey:
  1. SEARCH   → show all results numbered, ask "which one?"
  2. DETAILS  → show full property info, ask for name + phone
  3. COLLECT  → gather guest_name and guest_phone (one ask at a time)
  4. BOOK     → call create_booking only when ALL 6 fields are ready

Supports both large (tool-calling) and small (JSON fallback) LLMs.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent.state import AgentState
from agent.tools import ALL_TOOLS, TOOL_MAP


# ── LLM factory ───────────────────────────────────────────────────────────────

_llm_with_tools = None
_llm_plain = None


def _get_llm(with_tools: bool = True):
    global _llm_with_tools, _llm_plain
    cached = _llm_with_tools if with_tools else _llm_plain
    if cached is not None:
        return cached

    # Increase default tokens so full search results fit
    max_tokens = int(os.getenv("LLM_MAX_TOKENS", "1024"))
    base_url = os.getenv("LM_STUDIO_BASE_URL")

    if base_url:
        from langchain_openai import ChatOpenAI
        base = ChatOpenAI(
            base_url=base_url,
            api_key="lm-studio",
            model=os.getenv("LM_STUDIO_MODEL", "local-model"),
            temperature=0,
            max_tokens=max_tokens,
        )
    else:
        from langchain_groq import ChatGroq
        base = ChatGroq(
            api_key=os.environ["GROQ_API_KEY"],
            model=os.getenv("GROQ_MODEL", "llama3-70b-8192"),
            temperature=0,
            max_tokens=max_tokens,
        )

    if with_tools:
        _llm_with_tools = base.bind_tools(ALL_TOOLS)
        return _llm_with_tools
    else:
        _llm_plain = base
        return _llm_plain


# ── System prompt (large model) ───────────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """\
You are Riya, a warm and professional booking receptionist for StayEase — Bangladesh's top hotel rental platform.

TODAY: {today}

CURRENT BOOKING IN PROGRESS:
{pending_json}

══════════════════════════════════════════════════════════════
GUEST JOURNEY — follow these steps in strict order:
══════════════════════════════════════════════════════════════

STEP 1 ▶ SEARCH (user wants a room / hotel)
  • If location, check-in, check-out, or guests are missing → ask for them FIRST.
  • Then call: search_available_properties(location, check_in, check_out, guests)
  • Present ALL results as a numbered list, one property per line:
        1. Sea Pearl Beach Resort — ৳8,500/night — WiFi, Pool, Sea View — max 4 guests
        2. Long Beach Hotel      — ৳5,200/night — WiFi, AC, Sea View   — max 2 guests
  • End with: "Which property would you like to book? 😊"

STEP 2 ▶ PROPERTY SELECTED (user picks one by name or number)
  • Call: get_listing_details_by_name(listing_name)
  • Show: name, address, price/night, amenities, host contact.
  • Ask: "Great choice! To confirm your booking, please share your full name and phone number."

STEP 3 ▶ COLLECT GUEST INFO
  • If guest_name is missing in CURRENT BOOKING → ask: "May I have your full name please?"
  • If guest_phone is missing in CURRENT BOOKING → ask: "And your phone number? (e.g. +8801XXXXXXXXX)"
  • Do NOT skip this step. Do NOT proceed to Step 4 until BOTH are provided.

STEP 4 ▶ CONFIRM BOOKING (only when ALL 6 fields are ready)
  Required fields (check CURRENT BOOKING above):
    ✓ listing_id   ✓ check_in   ✓ check_out   ✓ guests   ✓ guest_name   ✓ guest_phone
  • Call: create_booking(listing_id, guest_name, guest_phone, check_in, check_out, guests)
  • Reply with full confirmation:
        ✅ Booking confirmed! 
        🏨 Property : Long Beach Hotel
        📅 Dates    : 2026-05-01 → 2026-05-03 (2 nights)
        👥 Guests   : 2
        💳 Total    : ৳10,400
        📋 Booking ID: #42
        "Thank you, Ruhul! Have a wonderful stay. 🌊"

══════════════════════════════════════════════════════════════
STRICT RULES:
  ❌ NEVER call create_booking without guest_name AND guest_phone.
  ❌ NEVER guess prices, IDs, or availability — always use tools.
  ❌ NEVER skip the search step — always show options before booking.
  ✅ Use ৳ for prices (e.g. ৳5,200/night, ৳10,400 total).
  ✅ Keep replies concise — max 8 sentences per turn.
  ✅ Unrelated questions → "I can only help with StayEase bookings. Call 16700 or email support@stayease.com.bd."
══════════════════════════════════════════════════════════════
"""

# ── System prompt (small local model JSON fallback) ───────────────────────────

SYSTEM_PROMPT_JSON = """You are Riya, a booking assistant for StayEase Bangladesh.
Output ONLY a single JSON object — no explanation, no markdown, no extra text.

JSON format:
{"intent": "<search|details|book|collect|escalate>", "tool_input": {}}

Intent rules:
- "search"  → user wants to find rooms
  tool_input: {"location": "...", "check_in": "YYYY-MM-DD", "check_out": "YYYY-MM-DD", "guests": N}
- "details" → user picks a property by name
  tool_input: {"listing_name": "<property name>"}
- "book"    → all info available: listing_id, guest_name, guest_phone, dates, guests
  tool_input: {"listing_id": N, "guest_name": "...", "guest_phone": "...", "check_in": "...", "check_out": "...", "guests": N}
- "collect" → waiting for guest name/phone → no tool call needed
  tool_input: {}
- "escalate" → unrelated question
  tool_input: {}
"""


# ── Helper: detect small model ────────────────────────────────────────────────

def _is_small_model() -> bool:
    model = os.getenv("LM_STUDIO_MODEL", "").lower()
    small_hints = ["1.5b", "1b", "3b", "deepseek-r1-distill-qwen-1.5", "qwen-1.5b"]
    return any(h in model for h in small_hints) or bool(os.getenv("FORCE_JSON_MODE"))


# ── Helper: JSON extractor ────────────────────────────────────────────────────

def _extract_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {"intent": "escalate", "tool_input": {}}


# ── Helper: build pending_booking summary for prompt ─────────────────────────

def _pending_summary(pending: dict) -> str:
    if not pending:
        return "None — waiting for guest's request."
    lines = []
    field_labels = {
        "location":     "Location",
        "check_in":     "Check-in",
        "check_out":    "Check-out",
        "guests":       "Guests",
        "listing_id":   "Listing ID",
        "listing_name": "Property",
        "guest_name":   "Guest Name",
        "guest_phone":  "Guest Phone",
    }
    for key, label in field_labels.items():
        val = pending.get(key)
        status = f"✓ {val}" if val else "✗ MISSING"
        lines.append(f"  {label:<14}: {status}")
    return "\n".join(lines)


# ── Helper: reply formatter for small-model JSON path ────────────────────────

def _format_reply(intent: str, tool_output: Any, error: str | None) -> str:
    if error:
        return f"Sorry, I ran into a problem: {error}. Please try again or call 16700."

    prompt_map = {
        "details": (
            f"Property data: {json.dumps(tool_output, default=str, ensure_ascii=False)}\n"
            "Write a friendly 3-4 sentence summary: name, location, price in ৳, "
            "max guests, top amenities, host contact. "
            "Then ask: 'To confirm your booking, please share your full name and phone number.'"
        ),
        "search": (
            f"Search results: {json.dumps(tool_output, default=str, ensure_ascii=False)}\n"
            "List each property numbered: 1. Name — ৳ price/night — top 2 amenities — max N guests. "
            "If empty, apologise and suggest widening the search. "
            "End with: 'Which property would you like to book? 😊'"
        ),
        "book": (
            f"Booking confirmation: {json.dumps(tool_output, default=str, ensure_ascii=False)}\n"
            "Write a warm confirmation: Booking ID, property name, check-in, check-out, "
            "total nights, guests, total ৳ price. Max 4 sentences."
        ),
    }

    user_prompt = prompt_map.get(
        intent,
        f"Data: {json.dumps(tool_output, default=str, ensure_ascii=False)}\nSummarise for the guest in 2 sentences."
    )
    llm = _get_llm(with_tools=False)
    result = llm.invoke([
        SystemMessage(content="You are Riya, StayEase assistant. Reply friendly and concise. No markdown headers."),
        HumanMessage(content=user_prompt),
    ])
    return result.content.strip()


# ── Node 1: input_node ────────────────────────────────────────────────────────

def input_node(state: AgentState) -> dict[str, Any]:
    """
    Rebuild the SystemMessage every turn so the LLM always sees the latest
    pending_booking status (updated across turns).
    """
    msgs = list(state.get("messages", []))

    # Remove any stale SystemMessage — we'll prepend a fresh one
    msgs = [m for m in msgs if not isinstance(m, SystemMessage)]

    pending = state.get("pending_booking") or {}

    if _is_small_model():
        prompt_text = SYSTEM_PROMPT_JSON + f"\nToday: {date.today().isoformat()}"
    else:
        prompt_text = SYSTEM_PROMPT_TEMPLATE.format(
            today=date.today().isoformat(),
            pending_json=_pending_summary(pending),
        )

    msgs = [SystemMessage(content=prompt_text)] + msgs
    return {"messages": msgs, "error": None}


# ── Node 2: agent_node ────────────────────────────────────────────────────────

def agent_node(state: AgentState) -> dict[str, Any]:
    if _is_small_model():
        return _agent_node_json(state)
    return _agent_node_tools(state)


def _agent_node_tools(state: AgentState) -> dict[str, Any]:
    """Path A: large tool-calling LLM."""
    llm = _get_llm(with_tools=True)
    ai_message: AIMessage = llm.invoke(state["messages"])

    # Safety net: LLM described tools instead of calling them → force retry
    if not ai_message.tool_calls and isinstance(ai_message.content, str):
        bad_phrases = [
            "parameters", "minLength", '"type": "object"',
            "Here's how", "use the `", "tool with", "search_listings",
        ]
        if any(p in ai_message.content for p in bad_phrases):
            retry = list(state["messages"]) + [
                SystemMessage(content="You must call the correct tool NOW. Do not write anything else.")
            ]
            ai_message = llm.invoke(retry)

    intent = _detect_intent(ai_message, state.get("intent", ""))

    # Snapshot any guest info the LLM is passing into create_booking
    pending = dict(state.get("pending_booking") or {})
    if ai_message.tool_calls:
        for call in ai_message.tool_calls:
            args = call.get("args", {})
            if call["name"] == "search_available_properties":
                pending.update({
                    "location":  args.get("location"),
                    "check_in":  str(args.get("check_in", "")),
                    "check_out": str(args.get("check_out", "")),
                    "guests":    args.get("guests"),
                })
            elif call["name"] == "create_booking":
                # Inject customer_id into the booking args so it's saved in DB
                call["args"]["customer_id"] = state.get("customer_id")
                pending.update({
                    "guest_name":  args.get("guest_name"),
                    "guest_phone": args.get("guest_phone"),
                })

    return {"messages": [ai_message], "intent": intent, "pending_booking": pending}


def _agent_node_json(state: AgentState) -> dict[str, Any]:
    """Path B: small model JSON fallback."""
    llm = _get_llm(with_tools=False)
    raw_ai: AIMessage = llm.invoke(state["messages"])
    raw_text = raw_ai.content or ""

    parsed = _extract_json(raw_text)
    intent = parsed.get("intent", "escalate")
    t_input = parsed.get("tool_input", {})

    tool_name_map = {
        "details": "get_listing_details_by_name",
        "search":  "search_available_properties",
        "book":    "create_booking",
    }
    tool_name = tool_name_map.get(intent)

    pending = dict(state.get("pending_booking") or {})

    if tool_name and t_input:
        import uuid
        synthetic = AIMessage(
            content="",
            tool_calls=[{"id": str(uuid.uuid4()), "name": tool_name, "args": t_input}],
        )
        return {"messages": [synthetic], "intent": intent, "tool_input": t_input, "pending_booking": pending}

    # collect / escalate — return a canned response
    if intent == "collect":
        missing = []
        if not pending.get("guest_name"):
            missing.append("full name")
        if not pending.get("guest_phone"):
            missing.append("phone number")
        ask = " and your ".join(missing)
        reply_text = f"Could you please share your {ask} to complete the booking?"
    else:
        reply_text = (
            "I can only help with StayEase property searches and bookings. "
            "For anything else please contact support@stayease.com.bd or call 16700."
        )

    escalate_msg = AIMessage(content=reply_text)
    return {"messages": [escalate_msg], "intent": intent, "pending_booking": pending}


def _detect_intent(ai_message: AIMessage, current: str) -> str:
    intent_map = {
        "search_available_properties":  "search",
        "get_listing_details":          "details",
        "get_listing_details_by_name":  "details",
        "search_listings_catalog":      "details",
        "create_booking":               "book",
    }
    if ai_message.tool_calls:
        return intent_map.get(ai_message.tool_calls[0]["name"], "details")
    return current or "escalate"


# ── Node 3: tool_node ─────────────────────────────────────────────────────────

def tool_node(state: AgentState) -> dict[str, Any]:
    """
    Execute tool calls and return ToolMessages.
    Also updates pending_booking with any newly discovered data
    (check_in/check_out from search, listing_id from details, cleared on successful book).
    """
    last_msg: AIMessage = state["messages"][-1]
    tool_messages = []
    result = None
    pending = dict(state.get("pending_booking") or {})

    for call in last_msg.tool_calls:
        tool_fn = TOOL_MAP.get(call["name"])
        if tool_fn is None:
            result = f"Error: tool '{call['name']}' not found."
        else:
            try:
                result = tool_fn.invoke(call["args"])
            except Exception as exc:
                result = f"Tool error: {exc}"

        # ── Update pending_booking from results ───────────────────────────
        args = call.get("args", {})

        if call["name"] == "search_available_properties":
            # Save search params for later booking
            pending.update({
                "location":  args.get("location", pending.get("location")),
                "check_in":  str(args.get("check_in", pending.get("check_in", ""))),
                "check_out": str(args.get("check_out", pending.get("check_out", ""))),
                "guests":    args.get("guests", pending.get("guests")),
            })

        elif call["name"] in ("get_listing_details", "get_listing_details_by_name"):
            if isinstance(result, dict) and result.get("listing_id"):
                pending["listing_id"]   = result["listing_id"]
                pending["listing_name"] = result.get("name", "")
                # Mark guest info as needed (ensure keys exist)
                pending.setdefault("guest_name", None)
                pending.setdefault("guest_phone", None)

        elif call["name"] == "create_booking":
            if isinstance(result, dict) and "booking_id" in result:
                # Booking successful — clear pending state
                pending = {}

        tool_messages.append(ToolMessage(
            content=json.dumps(result, default=str, ensure_ascii=False)
                    if not isinstance(result, str) else result,
            tool_call_id=call["id"],
            name=call["name"],
        ))

    updates: dict[str, Any] = {
        "messages":       tool_messages,
        "tool_output":    json.dumps(result, default=str, ensure_ascii=False)
                          if result is not None and not isinstance(result, str) else (result or ""),
        "pending_booking": pending,
    }

    # Small-model path: format the reply here so we skip the LLM loop
    if _is_small_model():
        intent = state.get("intent", "details")
        reply = _format_reply(intent, result, None)
        final_ai = AIMessage(content=reply)
        updates["messages"] = tool_messages + [final_ai]
        updates["final_response"] = reply

    return updates


# ── Node 4: response_node ─────────────────────────────────────────────────────

def response_node(state: AgentState) -> dict[str, Any]:
    """Extract the last AIMessage as the guest-facing reply."""
    if state.get("final_response"):
        return {}
    last_msg = state["messages"][-1]
    if isinstance(last_msg, AIMessage) and last_msg.content:
        return {"final_response": last_msg.content.strip()}
    return {"final_response": "Sorry, I couldn't process that. Please try again or call 16700."}


# ── Edge helper ───────────────────────────────────────────────────────────────

def should_continue(state: AgentState) -> str:
    """Route after agent_node: pending tool call → tool_node, else → response_node."""
    if state.get("final_response"):
        return "response_node"
    last_msg = state["messages"][-1]
    if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
        return "tool_node"
    return "response_node"
