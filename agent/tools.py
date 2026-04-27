"""
agent/tools.py
--------------
LangChain @tool definitions for the three core StayEase operations.
Each tool wraps a direct PostgreSQL query (via SQLAlchemy / psycopg2).
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field
import psycopg2
from psycopg2.extras import RealDictCursor


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_conn():
    """Return a new psycopg2 connection from DATABASE_URL env var."""
    return psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=RealDictCursor)


# ── Input schemas (Pydantic) ──────────────────────────────────────────────────

class SearchInput(BaseModel):
    """Parameters needed to search for available properties."""

    location: str = Field(
        ...,
        description="City or area in Bangladesh, e.g. 'Cox's Bazar', 'Sylhet', 'Sajek'",
    )
    check_in: date = Field(..., description="Arrival date in YYYY-MM-DD format")
    check_out: date = Field(..., description="Departure date in YYYY-MM-DD format")
    guests: int = Field(..., ge=1, le=20, description="Number of guests (1–20)")


class DetailsInput(BaseModel):
    """Parameters needed to fetch a single listing's full details."""

    listing_id: int = Field(..., description="Unique ID of the listing to look up")


class BookingInput(BaseModel):
    """Parameters needed to create a new booking."""

    listing_id: int = Field(..., description="Listing to book")
    guest_name: str = Field(..., description="Full name of the primary guest")
    guest_phone: str = Field(
        ..., description="Bangladeshi phone number, e.g. +8801888410789"
    )
    check_in: date = Field(..., description="Arrival date in YYYY-MM-DD format")
    check_out: date = Field(..., description="Departure date in YYYY-MM-DD format")
    guests: int = Field(..., ge=1, le=20, description="Number of guests")


# ── Tool 1 — search ───────────────────────────────────────────────────────────

@tool("search_available_properties", args_schema=SearchInput)
def search_available_properties(
    location: str,
    check_in: date,
    check_out: date,
    guests: int,
) -> list[dict[str, Any]]:
    """
    Search for listings in *location* that are available for the requested
    date range and can accommodate *guests* people.

    Returns a list of matching properties with their nightly price in BDT.
    Returns an empty list when no properties match.
    """
    sql = """
        SELECT
            l.id            AS listing_id,
            l.name,
            l.location,
            l.price_per_night_bdt,
            l.max_guests,
            l.amenities
        FROM listings l
        WHERE
            l.is_active = TRUE
            AND LOWER(l.location) LIKE LOWER(%(location)s)
            AND l.max_guests >= %(guests)s
            AND l.id NOT IN (
                SELECT b.listing_id
                FROM bookings b
                WHERE
                    b.status NOT IN ('cancelled')
                    AND b.check_in  < %(check_out)s
                    AND b.check_out > %(check_in)s
            )
        ORDER BY l.price_per_night_bdt ASC;
    """
    with _get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            {
                "location": f"%{location}%",
                "guests": guests,
                "check_in": check_in,
                "check_out": check_out,
            },
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


# ── Tool 2 — details ─────────────────────────────────────────────────────────

@tool("get_listing_details", args_schema=DetailsInput)
def get_listing_details(listing_id: int) -> dict[str, Any]:
    """
    Retrieve full details for a single listing by its ID.

    Returns a dict with name, address, description, price, amenities, host
    contact, and image URLs.  Returns an empty dict when not found.
    """
    sql = """
        SELECT
            id              AS listing_id,
            name,
            location,
            address,
            description,
            price_per_night_bdt,
            max_guests,
            amenities,
            host_name,
            host_phone
        FROM listings
        WHERE id = %(listing_id)s AND is_active = TRUE;
    """
    with _get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, {"listing_id": listing_id})
        row = cur.fetchone()
    return dict(row) if row else {}


# ── Tool 3 — book ─────────────────────────────────────────────────────────────

@tool("create_booking", args_schema=BookingInput)
def create_booking(
    listing_id: int,
    guest_name: str,
    guest_phone: str,
    check_in: date,
    check_out: date,
    guests: int,
) -> dict[str, Any]:
    """
    Create a new booking in the database after verifying availability.

    Calculates the total price from the listing's nightly rate and the number
    of nights, inserts a row into *bookings*, and returns a confirmation dict.
    Raises ValueError if the listing is unavailable for the requested dates.
    """
    nights = (check_out - check_in).days
    if nights <= 0:
        raise ValueError("check_out must be after check_in")

    with _get_conn() as conn, conn.cursor() as cur:
        # 1. Verify listing exists and fetch price
        cur.execute(
            "SELECT price_per_night_bdt, name FROM listings WHERE id = %s AND is_active = TRUE",
            (listing_id,),
        )
        listing = cur.fetchone()
        if not listing:
            raise ValueError(f"Listing {listing_id} not found or inactive")

        # 2. Double-check no overlapping confirmed booking (race-condition guard)
        cur.execute(
            """
            SELECT 1 FROM bookings
            WHERE listing_id = %s
              AND status NOT IN ('cancelled')
              AND check_in  < %s
              AND check_out > %s
            LIMIT 1
            """,
            (listing_id, check_out, check_in),
        )
        if cur.fetchone():
            raise ValueError("Property is no longer available for the selected dates")

        # 3. Insert booking
        total = float(listing["price_per_night_bdt"]) * nights
        cur.execute(
            """
            INSERT INTO bookings
                (listing_id, guest_name, guest_phone, check_in, check_out, guests, total_price_bdt, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'confirmed')
            RETURNING id
            """,
            (listing_id, guest_name, guest_phone, check_in, check_out, guests, total),
        )
        booking_id = cur.fetchone()["id"]
        conn.commit()

    return {
        "booking_id": booking_id,
        "listing_name": listing["name"],
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
        "nights": nights,
        "guests": guests,
        "total_price_bdt": total,
        "status": "confirmed",
    }


# ── Tool registry ─────────────────────────────────────────────────────────────

ALL_TOOLS = [search_available_properties, get_listing_details, create_booking]
TOOL_MAP: dict[str, Any] = {t.name: t for t in ALL_TOOLS}
