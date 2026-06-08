from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict

class AttemptBase(BaseModel):
    attempted_at: datetime
    http_status: Optional[int] = None
    outcome: str

class AttemptResponse(AttemptBase):
    id: int
    event_id: str

    model_config = ConfigDict(from_attributes=True)
