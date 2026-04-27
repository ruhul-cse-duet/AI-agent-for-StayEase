"""
main.py  (v4 — customer_id based, multi-conversation support)
--------------------------------------------------------------
Each customer (identified by customer_id) has their own conversation row.
One active conversation is kept per customer (the latest one).

API surface:
  POST /api/chat/message                       -> send a message
  GET  /api/chat/{conversation_id}/history     -> full message history
  GET  /api/chat/{conversation_id}/pending     -> current pending booking state
  GET  /api/customers/{customer_id}           → customer profile
  GET  /api/customers/{customer_id}/bookings  → all bookings for a customer
  GET  /health
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from dotenv import load_dotenv
load_dotenv()

import psycopg2
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage, AIMessage
from pydantic import BaseModel, Field
from psycopg2.extras import RealDictCursor

from agent.graph import run_agent
from agent.state import AgentState


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="StayEase AI Agent API",
    description="Riya — your StayEase booking assistant",
    version="4.0.0",
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


def _ensure_runtime_schema() -> None:
    """
    Lightweight runtime migration for older DBs.
    Keeps the chatbot API working even if schema.sql wasn't reapplied.
    """
    with _db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id          VARCHAR(50) PRIMARY KEY,
                name        VARCHAR(100),
                phone       VARCHAR(20),
                email       VARCHAR(150),
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cur.execute(
            """
            ALTER TABLE conversations
                ADD COLUMN IF NOT EXISTS customer_id VARCHAR(50),
                ADD COLUMN IF NOT EXISTS pending_booking JSONB NOT NULL DEFAULT '{}',
                ADD COLUMN IF NOT EXISTS intent_last VARCHAR(20),
                ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
            """
        )
        cur.execute(
            """
            ALTER TABLE bookings
                ADD COLUMN IF NOT EXISTS customer_id VARCHAR(50);
            """
        )
        conn.commit()


@app.on_event("startup")
def _startup_migration():
    _ensure_runtime_schema()


# ── Request / Response schemas ────────────────────────────────────────────────

class MessageRequest(BaseModel):
    conversation_id: str | None = None
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


class CustomerProfile(BaseModel):
    customer_id: str
    name: str | None
    phone: str | None
    email: str | None
    created_at: str


# ── Customer helpers ──────────────────────────────────────────────────────────

def _get_or_create_customer(customer_id: str) -> None:
    """
    Ensure a row exists in the customers table for this customer_id.
    Does nothing if already present (idempotent upsert).
    """
    with _db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO customers (id, updated_at)
            VALUES (%s, NOW())
            ON CONFLICT (id) DO UPDATE SET updated_at = NOW()
            """,
            (customer_id,),
        )
        conn.commit()


def _create_conversation(customer_id: str) -> str:
    """Create a new conversation row for this customer and return its id."""
    with _db() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                """
                INSERT INTO conversations (customer_id, messages, pending_booking)
                VALUES (%s, '[]', '{}')
                RETURNING id
                """,
                (customer_id,),
            )
            new_id = cur.fetchone()["id"]
        except Exception:
            # Legacy schema fallback: conversations.id is VARCHAR without default.
            conn.rollback()
            legacy_id = f"conv_{uuid.uuid4().hex[:12]}"
            cur.execute(
                """
                INSERT INTO conversations (id, customer_id, messages, pending_booking)
                VALUES (%s, %s, '[]', '{}')
                RETURNING id
                """,
                (legacy_id, customer_id),
            )
            new_id = cur.fetchone()["id"]
        conn.commit()
        return str(new_id)


def _create_new_session() -> tuple[str, str]:
    """
    Create a server-managed guest identity + conversation.
    Returns (customer_id, conversation_id).
    """
    customer_id = f"guest_{uuid.uuid4().hex[:12]}"
    _get_or_create_customer(customer_id)
    conversation_id = _create_conversation(customer_id)
    return customer_id, conversation_id


def _get_conversation_context(conversation_id: str) -> tuple[str, list[dict], dict[str, Any]]:
    """Returns (customer_id, messages, pending_booking) for a conversation row."""
    with _db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT customer_id, messages, pending_booking
            FROM conversations
            WHERE id::text = %s
            """,
            (str(conversation_id),),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Conversation '{conversation_id}' not found.")
    customer_id = row.get("customer_id")
    if not customer_id:
        customer_id = f"guest_legacy_{conversation_id}"
        _get_or_create_customer(customer_id)
        with _db() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE conversations SET customer_id = %s WHERE id::text = %s",
                (customer_id, str(conversation_id)),
            )
            conn.commit()
    return customer_id, row["messages"] or [], row["pending_booking"] or {}


# ── Conversation persistence ──────────────────────────────────────────────────

def _load_conversation(conversation_id: str) -> tuple[list[dict], dict[str, Any]]:
    """Returns (messages, pending_booking) for the given conversation row."""
    _, messages, pending_booking = _get_conversation_context(conversation_id)
    return messages, pending_booking


def _save_turn(
    conversation_id: str,
    customer_id: str,
    user_content: str,
    assistant_content: str,
    intent: str,
    pending_booking: dict[str, Any],
) -> None:
    """Append the latest turn and update pending_booking in the DB."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _db() as conn, conn.cursor() as cur:
            # Fetch current messages
            cur.execute(
                "SELECT messages FROM conversations WHERE id::text = %s",
                (str(conversation_id),),
            )
            row = cur.fetchone()
            existing: list = row["messages"] if row else []

            updated_messages = existing + [
                {"role": "user",      "content": user_content,     "timestamp": now},
                {"role": "assistant", "content": assistant_content, "timestamp": now},
            ]

            cur.execute(
                """
                UPDATE conversations
                SET messages        = %s,
                    pending_booking = %s,
                    intent_last     = %s,
                    updated_at      = NOW()
                WHERE id::text = %s
                """,
                (
                    json.dumps(updated_messages,  ensure_ascii=False),
                    json.dumps(pending_booking,   ensure_ascii=False, default=str),
                    intent,
                    str(conversation_id),
                ),
            )
            # Also update customer.updated_at
            cur.execute(
                "UPDATE customers SET updated_at = NOW() WHERE id = %s",
                (customer_id,),
            )
            conn.commit()
    except Exception as exc:
        print(f"[WARN] Could not save turn for conv {conversation_id}: {exc}")


def _clean_conversation_id(raw: str | None) -> str | None:
    if raw is None:
        return None
    cid = str(raw).strip()
    if cid.lower() in {"", "0", "null", "none", "undefined", "string"}:
        return None
    return cid


# ── Endpoint 1 — POST /api/chat/message ──────────────────────────────────────

@app.post(
    "/api/chat/message",
    response_model=MessageResponse,
)
def send_message(body: MessageRequest):
    """
    Send a guest message. The agent searches, collects guest info, and books.

    Multi-turn flow:
      Turn 1 → Search: "I need a room in Cox's Bazar for 2 nights for 2 guests"
               Agent shows all available properties with prices.
      Turn 2 → Pick:   "Long Beach Hotel"
               Agent shows full details, asks for name + phone.
      Turn 3 → Info:   "Ruhul Islam, +8801888410789"
               Agent calls create_booking and returns full confirmation.
    """
    # 1. Continue existing session, or start a new one
    conversation_id_in = _clean_conversation_id(body.conversation_id)
    if conversation_id_in is None:
        customer_id, conversation_id = _create_new_session()
        raw_history, pending_booking = [], {}
    else:
        conversation_id = conversation_id_in
        customer_id, raw_history, pending_booking = _get_conversation_context(conversation_id)

    # 4. Rebuild LangChain message list (no SystemMessage — input_node adds it)
    lc_messages = []
    for entry in raw_history:
        if entry["role"] == "user":
            lc_messages.append(HumanMessage(content=entry["content"]))
        else:
            lc_messages.append(AIMessage(content=entry["content"]))
    lc_messages.append(HumanMessage(content=body.content))

    # 5. Build initial agent state
    initial_state: AgentState = {
        "customer_id":     customer_id,
        "conversation_id": conversation_id,
        "messages":        lc_messages,
        "pending_booking": pending_booking,
        "intent":          "",
        "tool_input":      {},
        "tool_output":     "",
        "final_response":  "",
        "error":           None,
    }

    # 6. Run the LangGraph agent
    try:
        final_state: AgentState = run_agent(initial_state)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc

    reply       = final_state.get("final_response")  or ""
    intent_used = final_state.get("intent")           or "unknown"
    new_pending = final_state.get("pending_booking")  or {}

    # 7. If agent collected guest_name / guest_phone, backfill into customers table
    _update_customer_profile(customer_id, new_pending)

    # 8. Persist the turn
    _save_turn(conversation_id, customer_id, body.content, reply, intent_used, new_pending)

    return MessageResponse(
        conversation_id=conversation_id,
        reply=reply,
        intent=intent_used,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def _update_customer_profile(customer_id: str, pending: dict) -> None:
    """
    Once the agent collects the guest's name or phone, save it back to
    the customers row so future sessions can pre-fill it.
    """
    name  = pending.get("guest_name")
    phone = pending.get("guest_phone")
    if not name and not phone:
        return
    try:
        with _db() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE customers
                SET name       = COALESCE(%s, name),
                    phone      = COALESCE(%s, phone),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (name or None, phone or None, customer_id),
            )
            conn.commit()
    except Exception as exc:
        print(f"[WARN] Could not update customer profile: {exc}")


# ── Endpoint 2 — GET /api/chat/{conversation_id}/history ─────────────────────

@app.get(
    "/api/chat/{conversation_id}/history",
    response_model=HistoryResponse,
)
def get_history(conversation_id: str):
    """Return full history for a conversation id."""
    messages, _     = _load_conversation(conversation_id)
    return HistoryResponse(
        conversation_id=conversation_id,
        messages=[MessageEntry(**m) for m in messages],
    )


# ── Endpoint 3 — GET /api/chat/{conversation_id}/pending ─────────────────────

@app.get("/api/chat/{conversation_id}/pending")
def get_pending(conversation_id: str):
    """Debug: show what booking data the agent has collected so far."""
    customer_id, _, _ = _get_conversation_context(conversation_id)
    _, pending      = _load_conversation(conversation_id)
    return {
        "customer_id":     customer_id,
        "conversation_id": conversation_id,
        "pending_booking": pending,
    }


# ── Endpoint 4 — GET /api/customers/{customer_id} ────────────────────────────

@app.get(
    "/api/customers/{customer_id}",
    response_model=CustomerProfile,
)
def get_customer(customer_id: str):
    """Return the customer profile (name, phone, email, joined date)."""
    try:
        with _db() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, phone, email, created_at FROM customers WHERE id = %s",
                (customer_id,),
            )
            row = cur.fetchone()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if not row:
        raise HTTPException(status_code=404, detail=f"Customer '{customer_id}' not found.")

    return CustomerProfile(
        customer_id=row["id"],
        name=row["name"],
        phone=row["phone"],
        email=row["email"],
        created_at=row["created_at"].isoformat(),
    )


# ── Endpoint 5 — GET /api/customers/{customer_id}/bookings ───────────────────

@app.get("/api/customers/{customer_id}/bookings")
def get_customer_bookings(customer_id: str):
    """Return all bookings made by this customer."""
    try:
        with _db() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    b.id            AS booking_id,
                    l.name          AS property,
                    l.location,
                    b.check_in,
                    b.check_out,
                    b.guests,
                    b.total_price_bdt,
                    b.status,
                    b.created_at
                FROM bookings b
                JOIN listings l ON l.id = b.listing_id
                WHERE b.customer_id = %s
                ORDER BY b.created_at DESC
                """,
                (customer_id,),
            )
            rows = cur.fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {
        "customer_id": customer_id,
        "total": len(rows),
        "bookings": [dict(r) for r in rows],
    }


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "stayease-agent", "version": "4.0.0"}
