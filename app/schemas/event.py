from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, field_serializer

class EventCreate(BaseModel):
    type: str
    payload: Dict[str, Any]
    webhook_url: str

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("webhook_url must start with 'http://' or 'https://'")
        return v

class EventResponse(BaseModel):
    id: str
    type: str
    payload: Dict[str, Any] = Field(..., validation_alias="payload_json")
    webhook_url: str
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("status")
    def serialize_status(self, v: str) -> str:
        if v == "processing":
            return "pending"
        return v

class AttemptDetailResponse(BaseModel):
    attempted_at: datetime
    http_status: Optional[int] = None
    outcome: str

    model_config = ConfigDict(from_attributes=True)

class EventDetailResponse(BaseModel):
    id: str
    type: str
    payload: Dict[str, Any] = Field(..., validation_alias="payload_json")
    status: str
    webhook_url: str
    created_at: datetime
    attempts: List[AttemptDetailResponse]

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("status")
    def serialize_status(self, v: str) -> str:
        if v == "processing":
            return "pending"
        return v
