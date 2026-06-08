import os
import json
import hmac
import hashlib
import logging
from datetime import datetime, timedelta, timezone
import requests
from sqlalchemy.orm import Session

from app.models.event import Event, get_utc_now
from app.models.attempt import Attempt
from app.schemas.event import EventCreate

logger = logging.getLogger(__name__)

# Retry intervals in seconds:
# Retry 1: 30 seconds later
# Retry 2: 5 minutes later
# Retry 3: 30 minutes later
RETRY_INTERVALS = {
    1: 30,      # 30 seconds
    2: 300,     # 5 minutes
    3: 1800,    # 30 minutes
}

from typing import Union

def generate_signature(payload: Union[str, dict], secret: str) -> str:
    """Generates the HMAC-SHA256 signature using the exact raw JSON body."""
    if isinstance(payload, dict):
        payload_raw = json.dumps(payload, separators=(',', ':'), sort_keys=True)
    else:
        payload_raw = payload
    return hmac.new(
        secret.encode("utf-8"),
        payload_raw.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

def create_event(db: Session, event_data: EventCreate) -> Event:
    """Creates a new event in the database, scheduling it for immediate delivery."""
    utc_now = get_utc_now()
    
    # Store exact body as raw payload to ensure signature calculation is 100% stable
    payload_raw = json.dumps(event_data.payload, separators=(',', ':'), sort_keys=True)
    
    event = Event(
        type=event_data.type,
        payload_json=event_data.payload,
        payload_raw=payload_raw,
        webhook_url=event_data.webhook_url,
        status="pending",
        created_at=utc_now,
        next_retry_at=utc_now, # Set to now so it's ready for immediate attempt
        retry_count=0
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    logger.info(f"INFO Event created: {event.id}")
    return event

def deliver_event(db: Session, event_id: str) -> bool:
    """
    Attempts to deliver the webhook event.
    Returns True if delivery succeeded, False if failed.
    """
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event or event.status not in ["pending", "failed"]:
        return False

    # Mark as 'processing' immediately to prevent race conditions or duplicate deliveries
    event.status = "processing"
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to lock event {event_id} to processing: {str(e)}")
        return False

    secret = os.getenv("WEBHOOK_SECRET", "nestack-secret-key")
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": generate_signature(event.payload_raw, secret)
    }

    logger.info(f"Attempting delivery for event {event.id} to {event.webhook_url} (Attempt {event.retry_count + 1})")

    http_status = None
    outcome = "failure"
    success = False
    error_msg = None

    try:
        # Make the HTTP POST request sending the exact raw payload string
        response = requests.post(
            event.webhook_url,
            data=event.payload_raw.encode("utf-8"),
            headers=headers,
            timeout=10
        )
        http_status = response.status_code
        
        # Webhook returns any status code from 200-299 is considered a success
        if 200 <= response.status_code < 300:
            outcome = "success"
            success = True
        else:
            outcome = "failure"
            success = False
            error_msg = f"HTTP status {response.status_code}"
            
    except requests.exceptions.Timeout as e:
        error_msg = f"Timeout: {str(e)}"
        outcome = "failure"
        success = False
    except requests.exceptions.ConnectionError as e:
        error_msg = f"ConnectionError: {str(e)}"
        outcome = "failure"
        success = False
    except requests.exceptions.RequestException as e:
        error_msg = f"RequestException: {str(e)}"
        outcome = "failure"
        success = False

    # Create the attempt record
    attempt = Attempt(
        event_id=event.id,
        attempted_at=get_utc_now(),
        http_status=http_status,
        outcome=outcome
    )
    db.add(attempt)

    if success:
        # Update event status on success
        event.status = "delivered"
        event.next_retry_at = None
        logger.info(f"INFO Delivery success: Event {event.id}")
    else:
        # Increment retry count
        event.retry_count += 1
        
        if event.retry_count in RETRY_INTERVALS:
            delay_seconds = RETRY_INTERVALS[event.retry_count]
            event.status = "failed"
            event.next_retry_at = get_utc_now() + timedelta(seconds=delay_seconds)
            logger.warning(f"WARNING Delivery failed: Event {event.id} ({error_msg}). Retrying in {delay_seconds}s.")
        else:
            event.status = "dead"
            event.next_retry_at = None
            logger.error(f"ERROR Event dead: Event {event.id} (attempts exhausted)")

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to commit database updates for event {event_id}: {str(e)}")
        raise e

    return success

def retry_dead_event(db: Session, event_id: str) -> bool:
    """
    Manually retries a dead event.
    Only dead events can be retried.
    Returns True if successfully requeued, False otherwise.
    """
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event or event.status != "dead":
        return False

    # Reset retry scheduling parameters to initiate a fresh attempt cycle
    event.status = "pending"
    event.retry_count = 0
    event.next_retry_at = get_utc_now()
    
    try:
        db.commit()
        logger.info(f"INFO Event created: {event.id} (manual requeue)")
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to requeue event {event_id}: {str(e)}")
        return False
