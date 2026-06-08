# Nestack Webhook Delivery Engine

A production-ready, highly reliable Webhook Delivery Engine built with **FastAPI**, **SQLAlchemy**, and **SQLite**. The system performs database-backed webhook delivery and retry scheduling using a custom background worker thread.

### Live Links:
- **GitHub Repository**: [Jeevan_Nestack_Submission](https://github.com/woonnajeevan7-coder/Jeevan_Nestack_Submission)
- **Live Deployment URL**: [https://jeevan-nestack-submission.onrender.com](https://jeevan-nestack-submission.onrender.com)

---

## Architecture Overview

The system consists of three main components:
1. **FastAPI Web API**: Exposes endpoints to create/view events, track delivery attempts, and manually trigger retries.
2. **SQLite Database (via SQLAlchemy)**: Persists event states, retry schedules, and historical delivery attempt logs.
3. **Background Worker Thread**: A custom thread launched automatically on application startup that polls the database every second for due delivery and retry events.

```mermaid
graph TD
    Client[Client Application] -->|POST /events| API[FastAPI Web API]
    API -->|1. Write event (pending)| DB[(SQLite Database)]
    
    subgraph Custom Worker
        Thread[Background Worker Thread] -->|2. Poll every 1s for due events| DB
        Thread -->|3. Call requests POST| Target[Target Webhook URL]
    end

    Target -->|Response (2xx/non-2xx/Exception)| Result[Outcome Logger]
    Result -->|4. Log Attempt & Update Next Retry / Dead status| DB
```

---

## Technology Stack
- **Python 3.11+**
- **FastAPI**: Core API Framework
- **SQLAlchemy ORM**: Database access & mapping
- **SQLite**: Local relational database
- **Requests**: For webhook payload transmission
- **Uvicorn**: ASGI Server
- **Pytest**: Automated test suite

---

## Features
- **Immediate Webhook Delivery**: Webhook attempts are initiated concurrently right after event creation.
- **Robust Custom Retries**: Utilizes a strict fixed interval schedule (30s, 5m, 30m).
- **HMAC-SHA256 Request Signing**: Outgoing payloads are signed using SHA256 hashes generated from raw bodies.
- **Fail-safe Execution**: The system catches and records timeouts, DNS failures, connection drops, and HTTP errors without crashing.
- **Crash Persistence**: Active scheduling data survives server restarts. Overdue retries are processed immediately upon restart.
- **Manual Retry Endpoint**: Requeues events that have entered the `dead` state.

---

## Setup Instructions

### 1. Prerequisites
Ensure you have **Python 3.11** (or higher) installed.

### 2. Clone and Initialize Workspace
Open your terminal in the project directory.

```bash
# Create a virtual environment (recommended)
python -m venv venv

# Activate virtual environment
# On Windows:
.\venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate
```

### 3. Install Dependencies
Install all required libraries listed in `requirements.txt`:
```bash
pip install -r requirements.txt
```

### 4. Database Setup & Initialization
The database schema is automatically created when the FastAPI application starts. There is no need to run separate migration commands.

### 5. Configure Environment Variables
Copy the template `.env.example` to `.env`:
```bash
cp .env.example .env
```
Default configuration inside `.env`:
```env
DATABASE_URL=sqlite:///./webhook_engine.db
WEBHOOK_SECRET=nestack-secret-key
PORT=8000
HOST=127.0.0.1
```

---

## Running Locally

To start the API server along with the background worker thread, run:
```bash
uvicorn app.main:app --reload
```

The API will be accessible at: `http://127.0.0.1:8000`
Swagger UI Documentation: `http://127.0.0.1:8000/docs`

---

## Database Design

The database consists of two tables with a One-to-Many relationship between `Event` and `Attempt`.

### 1. `events` Table
| Column Name | Type | Description |
|---|---|---|
| `id` | String(36) | Primary Key (UUIDv4) |
| `type` | String | Type of event (e.g. `payment.failed`) |
| `payload_json` | JSON | Event payload parsed as dictionary |
| `payload_raw` | String | Compact serialized representation used for HMAC signature and transmission |
| `webhook_url` | String | Target webhook receiver URL |
| `status` | String | State: `pending`, `processing`, `delivered`, `failed`, `dead` |
| `created_at` | DateTime | Timestamp of event creation (UTC) |
| `next_retry_at`| DateTime | Scheduled timestamp of the next delivery attempt (UTC) |
| `retry_count` | Integer | Counter representing failed retry counts (max 4 attempts) |

*Note: The table is optimized with a composite index `ix_events_status_next_retry_at` on `(status, next_retry_at)` to support high-performance worker polling queries.*

### 2. `attempts` Table
| Column Name | Type | Description |
|---|---|---|
| `id` | Integer | Primary Key (Autoincrement) |
| `event_id` | String(36) | Foreign Key -> `events.id` (On Delete Cascade) |
| `attempted_at`| DateTime | Timestamp of the attempt (UTC) |
| `http_status` | Integer | HTTP response status code (null on network failures) |
| `outcome` | String | Result: `success`, `failure` |

---

## Delivery and Retry Scheduling Explanation

### State Workflow
To prevent duplicate execution and ensure state integrity, the system uses the following status definitions:
- `pending`: Waiting for the initial immediate delivery attempt.
- `processing`: (Internal Only) An attempt is actively running (preventing overlapping runs or double processing). To match the strict specification API responses, this status is serialized and returned as `"pending"` in all public endpoints.
- `delivered`: Delivery succeeded (HTTP 2xx returned).
- `failed`: Delivery failed, but scheduled retries remain.
- `dead`: All retries are exhausted (max 4 attempts executed).

### Retry Schedule:
1. **Initial Attempt**: Executed immediately (within 1 second) upon event creation by the background worker.
2. **Retry 1**: Occurs **30 seconds** after the initial attempt fails (status becomes `failed`).
3. **Retry 2**: Occurs **5 minutes** after Retry 1 fails (status remains `failed`).
4. **Retry 3**: Occurs **30 minutes** after Retry 2 fails (status remains `failed`).
5. **Mark as Dead**: If Retry 3 fails, the event status is marked as `dead` and no further attempts are scheduled.

### Duplicate Processing Prevention
Before initiating any outgoing webhook request, the event status is updated to `processing` and committed to the database. Even if the background worker loop ticks again, it ignores processing events. Once the request terminates, the status transitions to its final state (`delivered`, `failed`, or `dead`).

### Overdue/Restart Behavior & Crash Recovery:
All scheduling data (`retry_count`, `next_retry_at`, `status`) is fully persisted in the SQLite database. 
- **Lifespan Startup Recovery**: If the server crashes or shuts down while an event is in the `processing` state, it could get stuck. To prevent this, a startup recovery query runs immediately upon server reboot:
  ```sql
  UPDATE events SET status = 'pending' WHERE status = 'processing';
  ```
  This returns any interrupted deliveries safely back to the queue.
- **Worker Resume**: The custom background worker thread starts automatically upon server reboot. It queries the database for any events with `status` in `['pending', 'failed']` and `next_retry_at <= current_time`. If a retry was scheduled for 2:00 PM and the server starts at 2:05 PM, the worker immediately identifies the overdue event and triggers delivery.

---

## HMAC Signature Verification Guide

Every outgoing webhook request contains the header `X-Webhook-Signature`.
The signature is generated using **HMAC-SHA256** with the shared secret key (default: `nestack-secret-key`) based on the exact compact JSON bytes of the request body.

### Verification Examples

#### Python Verification Example
```python
import hmac
import hashlib
import json

def verify_signature(raw_body_bytes: bytes, received_signature: str, secret: str) -> bool:
    # Calculate the expected HMAC SHA256 digest
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        raw_body_bytes,
        hashlib.sha256
    ).hexdigest()
    
    # Securely compare signature strings to prevent timing attacks
    return hmac.compare_digest(expected_signature, received_signature)
```

#### Node.js Verification Example
```javascript
const crypto = require('crypto');

function verifySignature(rawBodyString, receivedSignature, secret) {
    const expectedSignature = crypto
        .createHmac('sha256', secret)
        .update(rawBodyString)
        .digest('hex');
        
    return crypto.timingSafeEqual(
        Buffer.from(expectedSignature, 'hex'),
        Buffer.from(receivedSignature, 'hex')
    );
}
```

---

## API Documentation

### 1. Create Webhook Event
- **Endpoint**: `POST /events`
- **Request Body**:
  ```json
  {
    "type": "payment.failed",
    "payload": {
      "amount": 100
    },
    "webhook_url": "http://127.0.0.1:8000/mock-webhook"
  }
  ```
- **Response (201 Created)**:
  ```json
  {
    "id": "2d942ab7-c533-4f9b-8d76-13a89ee18e19",
    "type": "payment.failed",
    "payload": {
      "amount": 100
    },
    "webhook_url": "http://127.0.0.1:8000/mock-webhook",
    "status": "pending",
    "created_at": "2026-06-08T08:00:00.000000"
  }
  ```

### 2. Get All Events
- **Endpoint**: `GET /events`
- **Response (200 OK)**:
  ```json
  [
    {
      "id": "2d942ab7-c533-4f9b-8d76-13a89ee18e19",
      "type": "payment.failed",
      "payload": {
        "amount": 100
      },
      "webhook_url": "http://127.0.0.1:8000/mock-webhook",
      "status": "delivered",
      "created_at": "2026-06-08T08:00:00.000000"
    }
  ]
  ```

### 3. Get Single Event Details
- **Endpoint**: `GET /events/{id}`
- **Response (200 OK)**:
  ```json
  {
    "id": "2d942ab7-c533-4f9b-8d76-13a89ee18e19",
    "type": "payment.failed",
    "payload": {
      "amount": 100
    },
    "status": "delivered",
    "webhook_url": "http://127.0.0.1:8000/mock-webhook",
    "created_at": "2026-06-08T08:00:00.000000",
    "attempts": [
      {
        "attempted_at": "2026-06-08T08:00:00.500000",
        "http_status": 200,
        "outcome": "success"
      }
    ]
  }
  ```

### 4. Retry Dead Event
- **Endpoint**: `POST /events/{id}/retry`
- **Response (200 OK)**:
  ```json
  {
    "message": "Event requeued"
  }
  ```
- **Response (400 Bad Request)** (if the event is not dead):
  ```json
  {
    "detail": "Only dead events may be retried"
  }
  ```

---

## Testing Guide

### 1. Local Mock Webhook Testing
We provide a helper mock receiver `/mock-webhook` built right into the app. It validates signatures and allows returning custom HTTP codes for testing.

1. Start the server:
   ```bash
   uvicorn app.main:app --reload
   ```
2. Create an event that will **succeed**:
   ```bash
   curl -X POST http://127.0.0.1:8000/events \
     -H "Content-Type: application/json" \
     -d '{"type": "user.created", "payload": {"user_id": 42}, "webhook_url": "http://127.0.0.1:8000/mock-webhook?status_code=200"}'
   ```
3. Create an event that will **fail** and verify retries:
   ```bash
   curl -X POST http://127.0.0.1:8000/events \
     -H "Content-Type: application/json" \
     -d '{"type": "payment.failed", "payload": {"amount": 250}, "webhook_url": "http://127.0.0.1:8000/mock-webhook?status_code=500"}'
   ```
   *Note: Querying the database or using `GET /events/{id}` over the next few minutes will show attempts being recorded with HTTP status 500, scheduled 30s, 5m, and 30m apart.*

### 2. Running Automated Unit Tests
To run the automated pytest suite (using a temporary DB and patched network calls):
```bash
pytest -v
```

---

## Deployment Configs

This codebase contains pre-built configuration files ready for zero-downtime hosting:

### Render Deployment (`render.yaml`)
Deploys with the Python standard environments. Database files are stored locally in the ephemeral disk. For persistent SQLite storage in production, mount a Render Disk volume and point `DATABASE_URL` to the mounted folder.

### Railway Deployment (`railway.json`)
Uses Nixpacks builder to automatically detect Python and configure the Uvicorn build step.

---

## Assumptions
- Webhook endpoints accept standard JSON payloads.
- Secret tokens for HMAC verification match between engine and subscriber (`nestack-secret-key`).
- For high-volume production, SQLite should be swapped for PostgreSQL by setting the `DATABASE_URL` env variable. No code modifications are needed due to SQLAlchemy database abstraction.
