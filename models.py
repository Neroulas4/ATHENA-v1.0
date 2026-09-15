from datetime import datetime, timezone
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

class Prediction(BaseModel):
    prediction_id: str
    subject: str
    thesis: str
    horizon: str
    confidence: float = Field(ge=0, le=1)
    trigger: str | None = None
    invalidation: str | None = None
    timestamp: datetime = Field(default_factory=now_utc)
    methodology_version: str = "0.9"

class Outcome(BaseModel):
    prediction_id: str
    evaluated_at: datetime = Field(default_factory=now_utc)
    thesis_correct: bool | None = None
    timing_correct: bool | None = None
    trigger_activated: bool | None = None
    execution_occurred: bool | None = None
    actual_result: str
    notes: str = ""

class LessonStatus(str, Enum):
    OBSERVED = "OBSERVED"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"

class Lesson(BaseModel):
    lesson_id: str
    statement: str
    status: LessonStatus = LessonStatus.OBSERVED
    observations: int = 0
    supporting_prediction_ids: list[str] = []
    created_at: datetime = Field(default_factory=now_utc)

class ResearchResult(BaseModel):
    run_id: str
    question: str
    facts: list[str] = []
    interpretations: list[str] = []
    specialist_findings: dict[str, str] = {}
    disagreements: list[str] = []
    risks: list[str] = []
    predictions: list[Prediction] = []
    confidence: float | None = None
    what_changes_view: list[str] = []
    trace_id: str | None = None
    errors: list[str] = []
