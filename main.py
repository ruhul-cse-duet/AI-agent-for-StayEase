"""
main.py
-------
FastAPI application for the StayEase AI booking agent.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Literal

from dotenv import load_dotenv
load_dotenv()   # ← .env ফাইল থেকে GROQ_API_KEY, DATABASE_URL লোড করে

import psycopg2
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from psycopg2.extras import RealDictCursor

from agent.graph import run_agent
from agent.nodes import SYSTEM_PROMPT
from agent.state import AgentState


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


def _db():
    return psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=RealDictCursor)


class MessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)


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


def _load_history(conversation_id: str) -> list[dict]:
    with _db() as conn, conn.cursor() as cur:
        cur.execute("SELECT messages FROM conversations WHERE id = %s", (conversation_id,))
        row = cur.fetchone()
    return row["messages"] if row else []


def _save_turn(conversation_id: str, user_content: str, assistant_content: str, intent: str):
    now = datetime.now(timezone.utc).isoformat()
    with _db() as conn, conn.cursor() as cur:
        cur.execute("SELECT messages FROM conversations WHERE id = %s", (conversation_id,))
        row = cur.fetchone()
        existing: list = row["messages"] if row else []
        updated = existing + [
            {"role": "user",      "content": user_content,     "timestamp": now},
            {"role": "assistant", "content": assistant_content, "timestamp": now},
        ]
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


@app.post("/api/chat/{conversation_id}/message", response_model=MessageResponse)
def send_message(conversation_id: str, body: MessageRequest):
    """Send a guest message and get an AI reply."""
    raw_history = _load_history(conversation_id)
    lc_messages = [SystemMessage(content=SYSTEM_PROMPT)]
    for entry in raw_history:
        cls = HumanMessage if entry["role"] == "user" else AIMessage
        lc_messages.append(cls(content=entry["content"]))
    lc_messages.append(HumanMessage(content=body.content))

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
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc

    reply       = final_state.get("final_response", "")
    intent_used = final_state.get("intent", "unknown")
    _save_turn(conversation_id, body.content, reply, intent_used)

    return MessageResponse(
        conversation_id=conversation_id,
        reply=reply,
        intent=intent_used,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/api/chat/{conversation_id}/history", response_model=HistoryResponse)
def get_history(conversation_id: str):
    """Return full conversation history."""
    messages = _load_history(conversation_id)
    return HistoryResponse(
        conversation_id=conversation_id,
        messages=[MessageEntry(**m) for m in messages],
    )


@app.get("/health")
def health():
    return {"status": "ok", "service": "stayease-agent"}
