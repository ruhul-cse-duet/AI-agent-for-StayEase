"""
main.py
-------
FastAPI application for the StayEase AI booking agent.

Endpoints
---------
POST /api/chat/{conversation_id}/message   — Send a guest message
GET  /api/chat/{conversation_id}/history   — Retrieve conversation history
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Literal

import psycopg2
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

from agent.graph import run_agent
from agent.nodes import SYSTEM_PROMPT
from agent.state import AgentState


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="StayEase AI Agent API",
    description="Natural-language booking assistant for StayEase Bangladesh",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── DB helper ─────────────────────────────────────────────────────────────────

def _db():
    return psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=RealDictCursor)


# ── Pydantic models ───────────────────────────────────────────────────────────

class MessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000, description="Guest message text")


class MessageEntry(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    timestamp: str


class MessageResponse(BaseModel):
    conversation_id: str
    reply: str
    intent: str
    timestamp: str


class HistoryResponse(BaseModel):
    conversation_id: str
    messages: list[MessageEntry]


# ── Persistence helpers ───────────────────────────────────────────────────────

def _load_history(conversation_id: str) -> list[dict]:
    """Return stored messages for a conversation, or []."""
    with _db() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT messages FROM conversations WHERE id = %s", (conversation_id,)
        )
        row = cur.fetchone()
    return row["messages"] if row else []


def _save_turn(conversation_id: str, user_content: str, assistant_content: str, intent: str):
    """Upsert the conversation row with the latest two messages."""
    now = datetime.now(timezone.utc).isoformat()
    with _db() as conn, conn.cursor() as cur:
        cur.execute("SELECT messages FROM conversations WHERE id = %s", (conversation_id,))
        row = cur.fetchone()
        existing: list = row["messages"] if row else []

        new_entries = [
            {"role": "user",      "content": user_content,      "timestamp": now},
            {"role": "assistant", "content": assistant_content,  "timestamp": now},
        ]
        updated = existing + new_entries

        cur.execute(
            """
            INSERT INTO conversations (id, messages, intent_last, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE
              SET messages    = EXCLUDED.messages,
                  intent_last = EXCLUDED.intent_last,
                  updated_at  = EXCLUDED.updated_at
            """,
            (conversation_id, json.dumps(updated), intent),
        )
        conn.commit()


# ── Endpoint 1 — POST /api/chat/{conversation_id}/message ────────────────────

@app.post(
    "/api/chat/{conversation_id}/message",
    response_model=MessageResponse,
    summary="Send a guest message and receive an AI reply",
)
def send_message(conversation_id: str, body: MessageRequest):
    """
    Accept a natural-language message from a StayEase guest and run the
    LangGraph agent to produce a reply.

    The agent handles search, details, and booking intents.
    Anything outside those three is escalated to human support.
    """
    # 1. Rebuild LangChain message history from DB
    raw_history = _load_history(conversation_id)
    lc_messages = [SystemMessage(content=SYSTEM_PROMPT)]
    for entry in raw_history:
        if entry["role"] == "user":
            lc_messages.append(HumanMessage(content=entry["content"]))
        else:
            lc_messages.append(AIMessage(content=entry["content"]))
    lc_messages.append(HumanMessage(content=body.content))

    # 2. Build initial state and run agent
    initial_state: AgentState = {
        "conversation_id": conversation_id,
        "messages":        lc_messages,
        "intent":          "",
        "tool_input":      {},
        "tool_output":     None,
        "final_response":  "",
        "error":           None,
    }

    try:
        final_state: AgentState = run_agent(initial_state)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc

    reply        = final_state.get("final_response", "")
    intent_used  = final_state.get("intent", "unknown")

    # 3. Persist turn
    _save_turn(conversation_id, body.content, reply, intent_used)

    return MessageResponse(
        conversation_id=conversation_id,
        reply=reply,
        intent=intent_used,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ── Endpoint 2 — GET /api/chat/{conversation_id}/history ─────────────────────

@app.get(
    "/api/chat/{conversation_id}/history",
    response_model=HistoryResponse,
    summary="Retrieve the full conversation history",
)
def get_history(conversation_id: str):
    """
    Return every message (user and assistant) for the given conversation_id.

    Returns an empty list when the conversation has not started yet.
    """
    messages = _load_history(conversation_id)
    return HistoryResponse(
        conversation_id=conversation_id,
        messages=[MessageEntry(**m) for m in messages],
    )


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "stayease-agent"}
