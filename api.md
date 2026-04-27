# StayEase Agent - API Contract

Base URL: `http://localhost:8000`  
Content-Type: `application/json`

---

## 1) Send Message (Chatbot Style)

### `POST /api/chat/message`

Send a natural-language message to the AI assistant.

`conversation_id` is optional:
- Omit it on the first message -> server creates a new chat session.
- Send it on next messages -> continues the same chat.

#### Request Body
```json
{
  "conversation_id": "12",
  "content": "I need a room in Cox's Bazar for 2 nights for 2 guests"
}
```

#### Response - `200 OK`
```json
{
  "conversation_id": "12",
  "reply": "I found 3 properties...",
  "intent": "search",
  "timestamp": "2026-04-27T12:00:00Z"
}
```

---

## 2) Get Chat History

### `GET /api/chat/{conversation_id}/history`

Returns the full ordered chat history.

#### Response
```json
{
  "conversation_id": "12",
  "messages": [
    {
      "role": "user",
      "content": "I need a room in Cox's Bazar...",
      "timestamp": "2026-04-27T12:00:00Z"
    },
    {
      "role": "assistant",
      "content": "I found 3 properties...",
      "timestamp": "2026-04-27T12:00:01Z"
    }
  ]
}
```

---

## 3) Get Pending Booking State (Debug)

### `GET /api/chat/{conversation_id}/pending`

Shows current collected booking info for that conversation.

#### Response
```json
{
  "customer_id": "guest_9f8a21c1bc2d",
  "conversation_id": "12",
  "pending_booking": {
    "location": "Cox's Bazar",
    "check_in": "2026-05-10",
    "check_out": "2026-05-12",
    "guests": 2
  }
}
```

---

## 4) Customer Profile (Internal/Backoffice)

### `GET /api/customers/{customer_id}`

Returns stored customer profile fields (`name`, `phone`, `email`).

---

## 5) Customer Bookings (Internal/Backoffice)

### `GET /api/customers/{customer_id}/bookings`

Returns all bookings linked to that customer id.

---

## 6) Health Check

### `GET /health`

```json
{
  "status": "ok",
  "service": "stayease-agent",
  "version": "4.0.0"
}
```
