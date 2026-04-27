# StayEase Agent — API Contract

Base URL: `http://localhost:8000`  
Content-Type: `application/json`

---

## Endpoint 1 — Send a Guest Message

### `POST /api/chat/{conversation_id}/message`

Sends a natural-language message from a guest to the AI agent and returns a reply.

#### Path Parameter
| Name | Type | Description |
|------|------|-------------|
| `conversation_id` | `string` | Client-generated session identifier (e.g. `conv_abc123`) |

#### Request Body
```json
{
  "content": "string (1–2000 chars, required)"
}
```

#### Response — `200 OK`
```json
{
  "conversation_id": "string",
  "reply":           "string",
  "intent":          "search | details | book | escalate | unknown",
  "timestamp":       "ISO-8601 UTC string"
}
```

---

### Example — Search Request (Cox's Bazar)

**Request**
```http
POST /api/chat/conv_bd_001/message
Content-Type: application/json

{
  "content": "I need a room in Cox's Bazar for 2 nights starting 2025-08-10 for 2 guests"
}
```

**Response**
```json
{
  "conversation_id": "conv_bd_001",
  "reply": "Great news! I found 3 properties in Cox's Bazar available for 10–12 August for 2 guests:\n\n1. **Sea Pearl Beach Resort** — ৳ 8,500/night | WiFi, AC, Pool\n2. **Long Beach Hotel** — ৳ 5,200/night | WiFi, AC, Sea View\n3. **Sayeman Beach Resort** — ৳ 4,800/night | WiFi, AC, Restaurant\n\nWould you like details on any of these, or shall I book one for you?",
  "intent": "search",
  "timestamp": "2025-08-08T10:23:45Z"
}
```

---

### Example — Booking Request

**Request**
```http
POST /api/chat/conv_bd_001/message
Content-Type: application/json

{
  "content": "Please book Long Beach Hotel for Rahim Uddin, phone 01711234567"
}
```

**Response**
```json
{
  "conversation_id": "conv_bd_001",
  "reply": "✅ Booking Confirmed!\n\nBooking ID: #1042\nProperty: Long Beach Hotel, Cox's Bazar\nGuest: Rahim Uddin\nCheck-in: 10 August 2025\nCheck-out: 12 August 2025\nGuests: 2\nTotal: ৳ 10,400\nStatus: Confirmed\n\nEnjoy your stay! 🏖️",
  "intent": "book",
  "timestamp": "2025-08-08T10:25:10Z"
}
```

---

### Example — Escalation (Out-of-Scope)

**Request**
```http
POST /api/chat/conv_bd_002/message
Content-Type: application/json

{
  "content": "Can you recommend a good restaurant near Sylhet?"
}
```

**Response**
```json
{
  "conversation_id": "conv_bd_002",
  "reply": "I'm sorry, I can only help with property searches, listing details, and bookings on StayEase. For anything else, please contact our support team at support@stayease.com.bd or call 16700.",
  "intent": "escalate",
  "timestamp": "2025-08-08T10:30:00Z"
}
```

---

### Error Responses — Endpoint 1

| HTTP Code | When | Body |
|-----------|------|------|
| `422 Unprocessable Entity` | `content` is empty or > 2000 chars | FastAPI default validation error |
| `500 Internal Server Error` | Agent crash / DB unreachable | `{"detail": "Agent error: <message>"}` |

---

## Endpoint 2 — Get Conversation History

### `GET /api/chat/{conversation_id}/history`

Returns the full ordered message history for a conversation.

#### Path Parameter
| Name | Type | Description |
|------|------|-------------|
| `conversation_id` | `string` | Session identifier used when sending messages |

#### Response — `200 OK`
```json
{
  "conversation_id": "string",
  "messages": [
    {
      "role":      "user | assistant",
      "content":   "string",
      "timestamp": "ISO-8601 UTC string"
    }
  ]
}
```
Returns `messages: []` when the conversation does not exist yet — no 404.

---

### Example — History for `conv_bd_001`

**Request**
```http
GET /api/chat/conv_bd_001/history
```

**Response**
```json
{
  "conversation_id": "conv_bd_001",
  "messages": [
    {
      "role": "user",
      "content": "I need a room in Cox's Bazar for 2 nights starting 2025-08-10 for 2 guests",
      "timestamp": "2025-08-08T10:23:44Z"
    },
    {
      "role": "assistant",
      "content": "Great news! I found 3 properties in Cox's Bazar...",
      "timestamp": "2025-08-08T10:23:45Z"
    },
    {
      "role": "user",
      "content": "Please book Long Beach Hotel for Rahim Uddin, phone 01711234567",
      "timestamp": "2025-08-08T10:25:09Z"
    },
    {
      "role": "assistant",
      "content": "✅ Booking Confirmed! Booking ID: #1042...",
      "timestamp": "2025-08-08T10:25:10Z"
    }
  ]
}
```

---

### Error Responses — Endpoint 2

| HTTP Code | When |
|-----------|------|
| `500 Internal Server Error` | Database unreachable |

---

## Health Check

### `GET /health`

```json
{ "status": "ok", "service": "stayease-agent" }
```
