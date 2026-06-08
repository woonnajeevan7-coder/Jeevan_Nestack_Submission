from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.event import Event
from app.schemas.event import EventCreate, EventResponse, EventDetailResponse
from app.services.delivery_service import create_event, retry_dead_event

router = APIRouter(prefix="/events", tags=["events"])

@router.post("", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
def create_new_event(
    event_data: EventCreate,
    db: Session = Depends(get_db)
):
    """
    Creates a new webhook event.
    The custom background worker thread will poll and process it immediately (within 1 second).
    """
    event = create_event(db, event_data)
    return event

@router.get("", response_model=List[EventResponse])
def get_all_events(db: Session = Depends(get_db)):
    """Returns a list of all webhook events ordered by creation date."""
    return db.query(Event).order_by(Event.created_at.desc()).all()

@router.get("/{id}", response_model=EventDetailResponse)
def get_single_event(id: str, db: Session = Depends(get_db)):
    """Returns detailed information of a single event including all delivery attempts."""
    event = db.query(Event).filter(Event.id == id).first()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event with id '{id}' not found"
        )
    return event

@router.post("/{id}/retry", status_code=status.HTTP_200_OK)
def retry_event(
    id: str,
    db: Session = Depends(get_db)
):
    """
    Manually retries a dead webhook event.
    Only dead events may be retried.
    """
    event = db.query(Event).filter(Event.id == id).first()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event with id '{id}' not found"
        )
    
    if event.status != "dead":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only dead events may be retried"
        )
    
    success = retry_dead_event(db, id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to requeue event"
        )
    
    return {"message": "Event requeued"}
