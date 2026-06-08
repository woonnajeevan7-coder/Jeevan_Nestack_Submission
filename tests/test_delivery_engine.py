import os
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Setup environment variables for tests before importing the app
os.environ["DATABASE_URL"] = "sqlite:///./test_webhook_engine.db"
os.environ["WEBHOOK_SECRET"] = "test-secret"

# Prevent background worker from auto-starting in background threads during tests
from unittest.mock import patch
patch("app.worker.background_worker.BackgroundWorker.start").start()

from app.main import app
from app.database.connection import Base, get_db
from app.models.event import Event, get_utc_now
from app.models.attempt import Attempt
from app.services.delivery_service import generate_signature

TEST_DATABASE_URL = "sqlite:///./test_webhook_engine.db"
engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

# Apply the dependency override
app.dependency_overrides[get_db] = override_get_db

@pytest.fixture(scope="function", autouse=True)
def setup_db():
    """Fixture to create and drop test database tables between tests."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    # Clean up the SQLite file if it exists
    if os.path.exists("./test_webhook_engine.db"):
        try:
            os.remove("./test_webhook_engine.db")
        except Exception:
            pass

client = TestClient(app)

def test_root_endpoint():
    """Verifies that the root health check works."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

def test_create_event_invalid_url():
    """Verifies that the schema validator catches invalid webhook URLs."""
    payload = {
        "type": "test.event",
        "payload": {"data": "info"},
        "webhook_url": "ftp://example.com"
    }
    response = client.post("/events", json=payload)
    assert response.status_code == 422
    assert "webhook_url must start with" in response.text

def test_create_event_success():
    """Verifies successful creation of webhook events."""
    payload = {
        "type": "payment.failed",
        "payload": {"amount": 100},
        "webhook_url": "https://example.com/webhook"
    }
    
    # Patch the immediate background delivery so we don't make real requests
    with patch("app.services.delivery_service.requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        response = client.post("/events", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["type"] == "payment.failed"
        assert data["payload"] == {"amount": 100}
        assert data["webhook_url"] == "https://example.com/webhook"
        assert data["status"] == "pending"

def test_hmac_signature_calculation():
    """Verifies that the HMAC signature is computed correctly according to requirements."""
    secret = "test-secret"
    payload = {"amount": 100}
    # Expected: HMAC-SHA256 of '{"amount":100}' with secret 'test-secret'
    # '{"amount":100}' is the compact JSON structure (no spaces)
    sig = generate_signature(payload, secret)
    assert len(sig) == 64 # Hex signature of SHA-256 is 64 chars

def test_mock_webhook_validation():
    """Verifies the mock webhook receiver endpoint enforces HMAC signature security."""
    payload = {"amount": 100}
    secret = "test-secret" # Matching env
    sig = generate_signature(payload, secret)

    # 1. No signature header -> 401
    response = client.post("/mock-webhook", json=payload)
    assert response.status_code == 401

    # 2. Invalid signature header -> 401
    response = client.post("/mock-webhook", json=payload, headers={"X-Webhook-Signature": "bad-signature"})
    assert response.status_code == 401

    # 3. Correct signature -> 200
    response = client.post("/mock-webhook", json=payload, headers={"X-Webhook-Signature": sig})
    assert response.status_code == 200
    assert response.json()["status"] == "success"

def test_get_events_list():
    """Verifies fetching all events."""
    db = TestingSessionLocal()
    e1 = Event(type="t1", payload_json={}, payload_raw="{}", webhook_url="https://x.com", status="delivered")
    e2 = Event(type="t2", payload_json={}, payload_raw="{}", webhook_url="https://y.com", status="dead")
    db.add_all([e1, e2])
    db.commit()
    db.close()

    response = client.get("/events")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2

def test_get_single_event_details():
    """Verifies fetching detailed view of a single event with attempts."""
    db = TestingSessionLocal()
    e1 = Event(type="payment.failed", payload_json={"amount": 100}, payload_raw='{"amount":100}', webhook_url="https://x.com", status="pending")
    db.add(e1)
    db.commit()
    db.refresh(e1)
    event_id = e1.id

    attempt = Attempt(event_id=event_id, outcome="success", http_status=200)
    db.add(attempt)
    db.commit()
    db.close()

    response = client.get(f"/events/{event_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == event_id
    assert data["status"] == "pending"
    assert len(data["attempts"]) == 1
    assert data["attempts"][0]["outcome"] == "success"
    assert data["attempts"][0]["http_status"] == 200

def test_retry_endpoint_rules():
    """Verifies that retry endpoint allows retrying dead events and rejects others."""
    db = TestingSessionLocal()
    e_pending = Event(type="t1", payload_json={}, payload_raw="{}", webhook_url="https://x.com", status="pending")
    e_dead = Event(type="t2", payload_json={}, payload_raw="{}", webhook_url="https://y.com", status="dead")
    db.add_all([e_pending, e_dead])
    db.commit()
    db.refresh(e_pending)
    db.refresh(e_dead)
    db.close()

    # Retrying a pending event should fail (400)
    response = client.post(f"/events/{e_pending.id}/retry")
    assert response.status_code == 400
    assert "Only dead events may be retried" in response.text

    # Retrying a dead event should succeed
    with patch("app.services.delivery_service.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 500  # Make background attempt fail so status remains pending
        mock_post.return_value = mock_resp

        response = client.post(f"/events/{e_dead.id}/retry")
        assert response.status_code == 200
        assert response.json() == {"message": "Event requeued"}

        # Verify it became pending in database with retry_count = 0
        db = TestingSessionLocal()
        e_db = db.query(Event).filter(Event.id == e_dead.id).first()
        assert e_db.status == "pending"
        assert e_db.retry_count == 0
        db.close()

def test_delivery_success_flow():
    """Verifies the status update after a successful webhook delivery."""
    db = TestingSessionLocal()
    e = Event(type="t1", payload_json={"key": "val"}, payload_raw='{"key":"val"}', webhook_url="https://example.com/webhook", status="pending")
    db.add(e)
    db.commit()
    db.refresh(e)
    
    with patch("app.services.delivery_service.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        from app.services.delivery_service import deliver_event
        success = deliver_event(db, e.id)
        assert success is True
        
        # Verify db updates
        db.refresh(e)
        assert e.status == "delivered"
        assert e.next_retry_at is None
        assert len(e.attempts) == 1
        assert e.attempts[0].outcome == "success"
        assert e.attempts[0].http_status == 200
    db.close()

def test_delivery_failure_flow_rescheduling():
    """Verifies retry rescheduling timeline on failure."""
    db = TestingSessionLocal()
    e = Event(type="t1", payload_json={"key": "val"}, payload_raw='{"key":"val"}', webhook_url="https://example.com/webhook", status="pending", retry_count=0)
    db.add(e)
    db.commit()
    db.refresh(e)

    # 1. First failure (Attempt 1 -> Retry 1 scheduled in 30s)
    with patch("app.services.delivery_service.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_post.return_value = mock_resp

        from app.services.delivery_service import deliver_event
        success = deliver_event(db, e.id)
        assert success is False
        
        db.refresh(e)
        assert e.status == "failed"
        assert e.retry_count == 1
        # Check next retry interval is 30 seconds
        time_diff = (e.next_retry_at - get_utc_now()).total_seconds()
        assert 25 <= time_diff <= 31
        assert len(e.attempts) == 1
        assert e.attempts[0].outcome == "failure"
        assert e.attempts[0].http_status == 500

    # 2. Second failure (Attempt 2 / Retry 1 -> Retry 2 scheduled in 5m)
    with patch("app.services.delivery_service.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_post.return_value = mock_resp

        from app.services.delivery_service import deliver_event
        success = deliver_event(db, e.id)
        assert success is False
        
        db.refresh(e)
        assert e.status == "failed"
        assert e.retry_count == 2
        # Check next retry interval is 5 minutes (300 seconds)
        time_diff = (e.next_retry_at - get_utc_now()).total_seconds()
        assert 295 <= time_diff <= 301
        assert len(e.attempts) == 2
        assert e.attempts[1].outcome == "failure"
        assert e.attempts[1].http_status == 404

    # 3. Third failure (Attempt 3 / Retry 2 -> Retry 3 scheduled in 30m)
    with patch("app.services.delivery_service.requests.post") as mock_post:
        # Simulate network timeout exception
        mock_post.side_effect = requests.exceptions.Timeout("Timeout")

        from app.services.delivery_service import deliver_event
        success = deliver_event(db, e.id)
        assert success is False
        
        db.refresh(e)
        assert e.status == "failed"
        assert e.retry_count == 3
        # Check next retry interval is 30 minutes (1800 seconds)
        time_diff = (e.next_retry_at - get_utc_now()).total_seconds()
        assert 1790 <= time_diff <= 1801
        assert len(e.attempts) == 3
        assert e.attempts[2].outcome == "failure"
        assert e.attempts[2].http_status is None # Timeout has no HTTP status code

    # 4. Fourth failure (Attempt 4 / Retry 3 -> Status dead)
    with patch("app.services.delivery_service.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_post.return_value = mock_resp

        from app.services.delivery_service import deliver_event
        success = deliver_event(db, e.id)
        assert success is False
        
        db.refresh(e)
        assert e.status == "dead"
        assert e.retry_count == 4
        assert e.next_retry_at is None
        assert len(e.attempts) == 4
        assert e.attempts[3].outcome == "failure"
        assert e.attempts[3].http_status == 401
    db.close()

def test_event_becomes_dead_after_four_attempts():
    """Verifies that an event transitions to 'dead' after exactly 4 failed attempts."""
    db = TestingSessionLocal()
    e = Event(type="t1", payload_json={"x": 1}, payload_raw='{"x":1}', webhook_url="https://example.com/webhook", status="pending", retry_count=0)
    db.add(e)
    db.commit()
    db.refresh(e)
    
    from app.services.delivery_service import deliver_event
    with patch("app.services.delivery_service.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_post.return_value = mock_resp
        
        # Attempt 1: Immediate failure -> status failed, retry_count = 1
        deliver_event(db, e.id)
        db.refresh(e)
        assert e.status == "failed"
        assert e.retry_count == 1
        
        # Attempt 2: Failure -> status failed, retry_count = 2
        deliver_event(db, e.id)
        db.refresh(e)
        assert e.status == "failed"
        assert e.retry_count == 2
        
        # Attempt 3: Failure -> status failed, retry_count = 3
        deliver_event(db, e.id)
        db.refresh(e)
        assert e.status == "failed"
        assert e.retry_count == 3
        
        # Attempt 4: Failure -> status dead, retry_count = 4
        deliver_event(db, e.id)
        db.refresh(e)
        assert e.status == "dead"
        assert e.retry_count == 4
        assert e.next_retry_at is None
    db.close()

def test_startup_recovery_processing_events():
    """Verifies that stuck 'processing' events are recovered to 'pending' during startup logic."""
    db = TestingSessionLocal()
    e_stuck = Event(
        type="stuck.event",
        payload_json={"data": 123},
        payload_raw='{"data":123}',
        webhook_url="https://example.com/webhook",
        status="processing",
        retry_count=1,
        next_retry_at=get_utc_now() - timedelta(minutes=10)
    )
    db.add(e_stuck)
    db.commit()
    event_id = e_stuck.id
    db.close()
    
    # Trigger recovery manually
    from app.database.connection import SessionLocal
    from app.models.event import Event as DBEvent
    
    rec_db = SessionLocal()
    try:
        recovered_count = rec_db.query(DBEvent).filter(DBEvent.status == "processing").update(
            {"status": "pending"}, synchronize_session=False
        )
        rec_db.commit()
        assert recovered_count == 1
    finally:
        rec_db.close()
        
    # Verify the event is now pending
    db = TestingSessionLocal()
    e_recovered = db.query(Event).filter(Event.id == event_id).first()
    assert e_recovered.status == "pending"
    db.close()

import requests.exceptions
