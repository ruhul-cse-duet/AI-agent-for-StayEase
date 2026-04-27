"""
agent/tools.py
--------------
LangChain @tool definitions for the three core StayEase operations.
Each tool wraps a direct PostgreSQL query.
"""

from __future__ import annotations

import os
import re
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


class DetailsByNameInput(BaseModel):
    """Parameters needed to fetch listing details by name."""

    listing_name: str = Field(..., min_length=2, description="Property name, e.g. 'Rose View Hotel Sylhet'")
    location: str | None = Field(default=None, description="Optional location hint, e.g. 'Sylhet'")


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
    customer_id: str | None = Field(default=None, description="Customer ID for the session (optional, linked automatically)")


class CatalogSearchInput(BaseModel):
    """Broad text search for listings when user asks general questions."""

    query: str = Field(..., min_length=1, description="Free-text query from user message")
    location: str | None = Field(default=None, description="Optional location filter")
    limit: int = Field(default=5, ge=1, le=10, description="Maximum number of rows")


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


@tool("get_listing_details_by_name", args_schema=DetailsByNameInput)
def get_listing_details_by_name(listing_name: str, location: str | None = None) -> dict[str, Any]:
    """
    Retrieve full details for a listing by fuzzy name (optionally narrowed by location).
    Returns {} when no active listing matches.
    """
    clean_name = re.sub(r"\s+", " ", listing_name.strip())
    clean_name = re.sub(
        r"\b(information|info|details?|tell me|please|about)\b",
        " ",
        clean_name,
        flags=re.IGNORECASE,
    )
    clean_name = re.sub(r"\s+", " ", clean_name).strip()
    clean_location = location.strip() if isinstance(location, str) and location.strip() else None

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
        WHERE
            is_active = TRUE
            AND (
                LOWER(name) LIKE LOWER(%(name_like)s)
                OR LOWER(%(name_text)s) LIKE CONCAT('%%', LOWER(name), '%%')
            )
            AND (
                %(location)s IS NULL
                OR LOWER(location) LIKE LOWER(%(location_like)s)
            )
        ORDER BY
            CASE
                WHEN LOWER(name) = LOWER(%(name_exact)s) THEN 0
                WHEN LOWER(name) LIKE LOWER(%(name_prefix)s) THEN 1
                WHEN LOWER(%(name_text)s) LIKE CONCAT('%%', LOWER(name), '%%') THEN 2
                ELSE 3
            END,
            LENGTH(name) ASC
        LIMIT 1;
    """
    with _get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            {
                "name_like": f"%{clean_name}%",
                "name_text": clean_name,
                "name_exact": clean_name,
                "name_prefix": f"{clean_name}%",
                "location": clean_location,
                "location_like": f"%{clean_location}%" if clean_location else None,
            },
        )
        row = cur.fetchone()
    return dict(row) if row else {}


@tool("search_listings_catalog", args_schema=CatalogSearchInput)
def search_listings_catalog(query: str, location: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
    """
    Search listings catalog by free text over name/location/address/description.
    Useful for receptionist-style "tell me about X" requests.
    """
    clean_query = re.sub(r"\s+", " ", query.strip())
    clean_location = location.strip() if isinstance(location, str) and location.strip() else None
    terms = [t for t in re.split(r"\s+", clean_query) if t]
    if not terms:
        return []

    with _get_conn() as conn, conn.cursor() as cur:
        # Build dynamic AND clauses for all terms so noisy phrases still match a known listing name.
        term_clauses = []
        params: dict[str, Any] = {
            "location": clean_location,
            "location_like": f"%{clean_location}%" if clean_location else None,
            "limit": limit,
            "whole_query": f"%{clean_query}%",
            "query_exact": clean_query,
        }
        for idx, term in enumerate(terms):
            key = f"term_{idx}"
            params[key] = f"%{term}%"
            term_clauses.append(
                f"""(
                    LOWER(l.name) LIKE LOWER(%({key})s)
                    OR LOWER(l.location) LIKE LOWER(%({key})s)
                    OR LOWER(COALESCE(l.address, '')) LIKE LOWER(%({key})s)
                    OR LOWER(COALESCE(l.description, '')) LIKE LOWER(%({key})s)
                )"""
            )

        term_sql = " OR ".join(term_clauses)
        sql = f"""
            SELECT
                l.id AS listing_id,
                l.name,
                l.location,
                l.address,
                l.description,
                l.price_per_night_bdt,
                l.max_guests,
                l.amenities
            FROM listings l
            WHERE
                l.is_active = TRUE
                AND (
                    %(location)s IS NULL
                    OR LOWER(l.location) LIKE LOWER(%(location_like)s)
                )
                AND (
                    LOWER(l.name) LIKE LOWER(%(whole_query)s)
                    OR ({term_sql})
                )
            ORDER BY
                CASE
                    WHEN LOWER(l.name) = LOWER(%(query_exact)s) THEN 0
                    WHEN LOWER(l.name) LIKE LOWER(%(whole_query)s) THEN 1
                    ELSE 2
                END,
                LENGTH(l.name) ASC
            LIMIT %(limit)s;
        """
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [dict(r) for r in rows]


# ── Tool 3 — book ─────────────────────────────────────────────────────────────

@tool("create_booking", args_schema=BookingInput)
def create_booking(
    listing_id: int,
    guest_name: str,
    guest_phone: str,
    check_in: date,
    check_out: date,
    guests: int,
    customer_id: str | None = None,
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

        # 3. Insert booking (with optional customer_id link)
        total = float(listing["price_per_night_bdt"]) * nights
        cur.execute(
            """
            INSERT INTO bookings
                (listing_id, customer_id, guest_name, guest_phone,
                 check_in, check_out, guests, total_price_bdt, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'confirmed')
            RETURNING id
            """,
            (listing_id, customer_id, guest_name, guest_phone,
             check_in, check_out, guests, total),
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

ALL_TOOLS = [
    search_available_properties,
    get_listing_details,
    get_listing_details_by_name,
    search_listings_catalog,
    create_booking,
]
TOOL_MAP: dict[str, Any] = {t.name: t for t in ALL_TOOLS}
