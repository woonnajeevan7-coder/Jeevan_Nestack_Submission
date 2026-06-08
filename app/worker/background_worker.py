import logging
import threading
from app.database.connection import SessionLocal
from app.models.event import Event, get_utc_now
from app.services.delivery_service import deliver_event

logger = logging.getLogger(__name__)

class BackgroundWorker(threading.Thread):
    """
    Background worker thread that runs continuously.
    It polls the SQLite database every second for due pending events and processes them.
    """
    def __init__(self):
        super().__init__()
        self._stop_event = threading.Event()
        self.daemon = True # Daemon thread ends automatically when the main thread exits
        self.name = "WebhookDeliveryWorker"

    def stop(self) -> None:
        """Signals the worker thread to stop processing and exit."""
        logger.info("Signaling background worker thread to stop...")
        self._stop_event.set()

    def run(self) -> None:
        logger.info("Custom background worker thread started.")
        
        while not self._stop_event.is_set():
            db = SessionLocal()
            try:
                utc_now = get_utc_now()
                # Find all events in 'pending' or 'failed' status that are scheduled for execution
                due_events = db.query(Event).filter(
                    Event.status.in_(["pending", "failed"]),
                    Event.next_retry_at <= utc_now
                ).all()

                if due_events:
                    logger.info(f"Worker polling: found {len(due_events)} due events to process.")
                    for event in due_events:
                        if self._stop_event.is_set():
                            break
                        try:
                            # Deliver each event safely. Internal service method commits updates.
                            deliver_event(db, event.id)
                        except Exception as e:
                            logger.exception(f"Unhandled error processing event {event.id}: {str(e)}")
            except Exception as e:
                logger.exception(f"Error in background worker main loop query: {str(e)}")
            finally:
                db.close()

            # Wait for 1 second, or wake up immediately if stop event is set
            self._stop_event.wait(timeout=1.0)

        logger.info("Custom background worker thread terminated.")
