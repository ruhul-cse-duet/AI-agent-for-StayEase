# StayEase AI Agent — Architecture Document

## 1.1 System Overview

StayEase is a short-term accommodation rental platform in Bangladesh. This AI agent lets guests
search for available properties, retrieve listing details, and create bookings entirely through
natural-language chat. Guest messages arrive via a FastAPI REST backend, which hands the conversation
to a LangGraph agent. The agent decides which tool to call (search / details / book) against a
PostgreSQL database and replies in Bengali-friendly English with prices in BDT. Anything outside
these three intents is escalated to a human operator.

```mermaid
flowchart TD
    Guest(["👤 Guest (Chat Client)"])
    API["FastAPI Backend\n/api/chat/{id}/message\n/api/chat/{id}/history"]
    Agent["LangGraph Agent\n(Groq / LM Studio LLM)"]
    Router{{"Intent Router\nNode"}}
    SearchTool["🔍 search_available_properties"]
    DetailsTool["📋 get_listing_details"]
    BookTool["📝 create_booking"]
    DB[("PostgreSQL\nlistings · bookings · conversations")]
    Human["🧑‍💼 Human Operator\n(Escalation)"]

    Guest -->|"POST /message"| API
    API -->|"run_agent()"| Agent
    Agent --> Router
    Router -->|"search intent"| SearchTool
    Router -->|"details intent"| DetailsTool
    Router -->|"book intent"| BookTool
    Router -->|"unknown intent"| Human
    SearchTool --> DB
    DetailsTool --> DB
    BookTool --> DB
    DB --> Agent
    Agent -->|"reply"| API
    API -->|"JSON response"| Guest
```

---

## 1.2 Conversation Flow — End-to-End Example

**Guest says:** *"I need a room in Cox's Bazar for 2 nights for 2 guests"*

| Step | Who | What happens |
|------|-----|--------------|
| 1 | Guest | Sends POST `/api/chat/conv_001/message` with body `{"role":"user","content":"I need a room in Cox's Bazar for 2 nights for 2 guests"}` |
| 2 | FastAPI | Persists message to `conversations` table, calls `run_agent(state)` |
| 3 | `input_node` | Loads conversation history from DB, builds initial `AgentState` |
| 4 | `intent_router` | LLM classifies intent → **search**; extracts `location="Cox's Bazar"`, `check_in`, `check_out` (2 nights from today), `guests=2` |
| 5 | `tool_executor` | Calls `search_available_properties(location="Cox's Bazar", check_in=..., check_out=..., guests=2)` |
| 6 | PostgreSQL | Returns rows from `listings` not blocked by `bookings` for those dates |
| 7 | `response_node` | LLM formats results: property names, nightly rates in BDT, amenities |
| 8 | FastAPI | Saves assistant reply to `conversations`, returns JSON to Guest |
| 9 | Guest | Sees: *"Found 3 properties in Cox's Bazar: Sea Pearl Beach Resort (৳ 8,500/night), ..."* |

---

## 1.3 LangGraph State Design

```python
class AgentState(TypedDict):
    conversation_id: str        # Identifies the session; links to DB conversations table
    messages: list[BaseMessage] # Full chat history fed to the LLM each turn
    intent: str                 # Classified intent: "search" | "details" | "book" | "escalate"
    tool_input: dict            # Structured params extracted for the chosen tool
    tool_output: Any            # Raw result returned by the tool
    final_response: str         # Human-readable reply sent back to the guest
    error: str | None           # Non-None when a recoverable error occurred in any node
```

| Field | Why it's needed |
|-------|-----------------|
| `conversation_id` | Ties every state update back to the correct DB row |
| `messages` | LLM needs full history to maintain context across turns |
| `intent` | Controls which branch the router takes after classification |
| `tool_input` | Decouples extraction from execution; keeps nodes single-responsibility |
| `tool_output` | Passed to `response_node` so the LLM can format it for the guest |
| `final_response` | Written back to the DB and returned via FastAPI |
| `error` | Enables graceful error messages without crashing the graph |

---

## 1.4 Node Design

### Node 1 — `input_node`
- **Does:** Loads conversation history from PostgreSQL and initialises `AgentState.messages`.
- **Updates:** `messages`
- **Next:** `intent_router`

### Node 2 — `intent_router`
- **Does:** Calls the LLM to classify the guest's latest message into one of four intents and extracts tool parameters.
- **Updates:** `intent`, `tool_input`
- **Next:** `tool_executor` (search/details/book) or `escalation_node` (unknown)

### Node 3 — `tool_executor`
- **Does:** Dispatches to the correct tool function based on `intent` and writes the result to state.
- **Updates:** `tool_output`, `error`
- **Next:** `response_node`

### Node 4 — `response_node`
- **Does:** Passes `tool_output` to the LLM to generate a friendly, BDT-priced reply in the guest's language.
- **Updates:** `final_response`, `messages`
- **Next:** `END`

### Node 5 — `escalation_node`
- **Does:** Sets a canned escalation message and flags the conversation for a human operator.
- **Updates:** `final_response`
- **Next:** `END`

---

## 1.5 Tool Definitions

### `search_available_properties`
| Item | Detail |
|------|--------|
| **Input** | `location: str`, `check_in: date`, `check_out: date`, `guests: int` |
| **Output** | `list[dict]` — each dict: `{listing_id, name, location, price_per_night_bdt, max_guests, amenities}` |
| **When used** | Guest mentions a place, dates, and headcount — intent classified as **search** |

### `get_listing_details`
| Item | Detail |
|------|--------|
| **Input** | `listing_id: int` |
| **Output** | `dict` — full listing row: `{listing_id, name, location, address, description, price_per_night_bdt, max_guests, amenities, images, host_name, host_phone}` |
| **When used** | Guest asks "tell me more about property #3" or names a specific property — intent classified as **details** |

### `create_booking`
| Item | Detail |
|------|--------|
| **Input** | `listing_id: int`, `guest_name: str`, `guest_phone: str`, `check_in: date`, `check_out: date`, `guests: int` |
| **Output** | `dict` — `{booking_id, listing_name, check_in, check_out, guests, total_price_bdt, status}` |
| **When used** | Guest explicitly confirms they want to book a specific property — intent classified as **book** |

---

## 1.6 Database Schema

### `listings`
| Column | Type | Notes |
|--------|------|-------|
| `id` | `SERIAL PRIMARY KEY` | Auto-increment listing ID |
| `name` | `VARCHAR(200)` | Property name |
| `location` | `VARCHAR(100)` | City / area (e.g. "Cox's Bazar") |
| `address` | `TEXT` | Full address |
| `description` | `TEXT` | Long-form description |
| `price_per_night_bdt` | `NUMERIC(10,2)` | Nightly rate in BDT |
| `max_guests` | `INT` | Maximum occupancy |
| `amenities` | `JSONB` | e.g. `["WiFi","AC","Pool"]` |
| `host_name` | `VARCHAR(100)` | Host contact name |
| `host_phone` | `VARCHAR(20)` | Host contact number |
| `is_active` | `BOOLEAN` | Soft-delete / deactivation flag |
| `created_at` | `TIMESTAMPTZ` | Row creation timestamp |

### `bookings`
| Column | Type | Notes |
|--------|------|-------|
| `id` | `SERIAL PRIMARY KEY` | Auto-increment booking ID |
| `listing_id` | `INT REFERENCES listings(id)` | FK to listings |
| `guest_name` | `VARCHAR(100)` | Guest full name |
| `guest_phone` | `VARCHAR(20)` | Guest contact number |
| `check_in` | `DATE` | Arrival date |
| `check_out` | `DATE` | Departure date |
| `guests` | `INT` | Number of guests |
| `total_price_bdt` | `NUMERIC(12,2)` | Computed at booking time |
| `status` | `VARCHAR(20)` | `pending` / `confirmed` / `cancelled` |
| `created_at` | `TIMESTAMPTZ` | Booking creation timestamp |

### `conversations`
| Column | Type | Notes |
|--------|------|-------|
| `id` | `VARCHAR(100) PRIMARY KEY` | Client-supplied conversation ID |
| `messages` | `JSONB` | Array of `{role, content, timestamp}` objects |
| `intent_last` | `VARCHAR(20)` | Last classified intent |
| `escalated` | `BOOLEAN` | True if handed off to human |
| `created_at` | `TIMESTAMPTZ` | Conversation start time |
| `updated_at` | `TIMESTAMPTZ` | Last message time |

---

## Running Locally

```bash
# 1. Clone & activate venv
python -m venv .venv && .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set environment variables
set GROQ_API_KEY=your_groq_key   # or LM Studio base URL in .env
set DATABASE_URL=postgresql://user:pass@localhost:5432/stayease

# 4. Run FastAPI
python -m uvicorn main:app --reload
```
