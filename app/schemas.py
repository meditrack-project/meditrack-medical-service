import uuid
from datetime import date, datetime
from typing import Optional, List

from pydantic import BaseModel, Field

ALLOWED_FREQUENCIES = [
    "once daily", "twice daily", "three times daily",
    "every 6 hours", "every 8 hours", "weekly", "as needed",
]

ALLOWED_TIMES = ["morning", "afternoon", "evening", "night", "multiple"]


class MedicationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    dosage: str = Field(..., min_length=1, max_length=50)
    frequency: str = Field(..., min_length=1, max_length=50)
    time_of_day: Optional[str] = None
    start_date: date
    end_date: Optional[date] = None
    notes: Optional[str] = None


class MedicationUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    dosage: Optional[str] = Field(None, min_length=1, max_length=50)
    frequency: Optional[str] = Field(None, min_length=1, max_length=50)
    time_of_day: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    notes: Optional[str] = None


class MedicationResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    dosage: str
    frequency: str
    time_of_day: Optional[str] = None
    start_date: date
    end_date: Optional[date] = None
    notes: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class MedicationLogResponse(BaseModel):
    id: uuid.UUID
    medication_id: uuid.UUID
    date: date
    taken: bool
    taken_at: Optional[datetime] = None
    notes: Optional[str] = None

    model_config = {"from_attributes": True}
