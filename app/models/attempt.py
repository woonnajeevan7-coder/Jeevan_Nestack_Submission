from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.database.connection import Base
from app.models.event import get_utc_now

class Attempt(Base):
    __tablename__ = "attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(36), ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    attempted_at = Column(DateTime, nullable=False, default=get_utc_now)
    
    # http_status can be null if a connection/timeout/DNS error occurred
    http_status = Column(Integer, nullable=True)
    
    # Outcome can be: 'success', 'failure'
    outcome = Column(String(50), nullable=False)

    event = relationship("Event", back_populates="attempts")
