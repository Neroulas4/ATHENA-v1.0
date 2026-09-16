"""Validated domain models used by every ATHENA entry point."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"
    WATCH = "WATCH"


class ActivationState(str, Enum):
    ACTIVE = "ACTIVE"
    NOT_ACTIVE_YET = "NOT_ACTIVE_YET"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class SetupState(str, Enum):
    NOT_ACTIVE_YET = "NOT_ACTIVE_YET"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ExecutionState(str, Enum):
    NOT_EXECUTED = "NOT_EXECUTED"
    EXECUTED = "EXECUTED"
    UNKNOWN = "UNKNOWN"


class PredictionStatus(str, Enum):
    OPEN = "OPEN"
    EVALUATED = "EVALUATED"
    EXPIRED = "EXPIRED"


class LessonStatus(str, Enum):
    OBSERVED = "OBSERVED"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"


class Prediction(BaseModel):
    """Immutable snapshot of a forward-looking ATHENA claim."""
    model_config = ConfigDict(frozen=True)
    prediction_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=now_utc)
    subject: str
    instrument: str | None = None
    direction: Direction = Direction.WATCH
    thesis: str
    horizon: str
    confidence: float = Field(ge=0, le=1)
    catalyst: str | None = None
    context: str | None = None
    evidence_references: tuple[str, ...] = ()
    supporting_specialists: tuple[str, ...] = ()
    entry_condition: str | None = None
    entry_reference_level: Decimal | None = None
    trigger_operator: str | None = None
    stop_level: Decimal | None = None
    stop_invalidation: str | None = None
    tp1: Decimal | None = None
    tp2: Decimal | None = None
    expected_value: Decimal | None = None
    quality_score: float | None = Field(default=None, ge=0, le=1)
    risk_reward: Decimal | None = None
    dissenting_specialists: tuple[str, ...] = ()
    status: PredictionStatus = PredictionStatus.OPEN
    activation_state: ActivationState = ActivationState.NOT_ACTIVE_YET
    execution_state: ExecutionState = ExecutionState.NOT_EXECUTED
    created_from_run: str | None = None
    methodology_tags: tuple[str, ...] = ()
    setup_state: SetupState = SetupState.NOT_ACTIVE_YET

    @model_validator(mode="after")
    def valid_levels(self) -> "Prediction":
        if self.timestamp.tzinfo is None:
            raise ValueError("Prediction timestamp must be timezone-aware")
        if self.horizon.strip() == "":
            raise ValueError("Prediction horizon is required")
        if self.trigger_operator not in (None, "ABOVE", "BELOW"):
            raise ValueError("trigger_operator must be ABOVE or BELOW")
        if self.direction in (Direction.LONG, Direction.SHORT) and self.entry_reference_level is not None:
            if self.entry_reference_level <= 0:
                raise ValueError("entry_reference_level must be positive")
            if self.stop_level is not None:
                if self.direction == Direction.LONG and self.stop_level >= self.entry_reference_level:
                    raise ValueError("LONG stop must be below entry")
                if self.direction == Direction.SHORT and self.stop_level <= self.entry_reference_level:
                    raise ValueError("SHORT stop must be above entry")
        return self


class Outcome(BaseModel):
    model_config = ConfigDict(frozen=True)
    outcome_id: str = Field(default_factory=lambda: str(uuid4()))
    prediction_id: str
    evaluated_at: datetime = Field(default_factory=now_utc)
    actual_result: str
    realized_move: Decimal | None = None
    maximum_adverse_excursion: Decimal | None = None
    maximum_favorable_excursion: Decimal | None = None
    stop_hit: bool | None = None
    tp1_hit: bool | None = None
    tp2_hit: bool | None = None
    horizon_expired: bool | None = None
    evaluation_confidence: float | None = Field(default=None, ge=0, le=1)
    trigger_activated: bool | None = None
    activated_at: datetime | None = None
    execution_occurred: bool | None = None
    thesis_correct: bool | None = None
    timing_correct: bool | None = None
    outcome_status: str = "PENDING"
    setup_state: SetupState = SetupState.NOT_ACTIVE_YET
    data_quality: str = "UNKNOWN"
    notes: str = ""

    @model_validator(mode="after")
    def untriggered_is_not_a_trade(self) -> "Outcome":
        if self.evaluated_at.tzinfo is None:
            raise ValueError("Outcome timestamp must be timezone-aware")
        if self.trigger_activated is False and self.execution_occurred is True:
            raise ValueError("An untriggered setup cannot have executed")
        if self.activated_at is not None and self.activated_at.tzinfo is None:
            raise ValueError("Activation timestamp must be timezone-aware")
        return self


class LessonContext(BaseModel):
    """Optional scope for a methodological pattern; absent fields are unknown."""
    model_config = ConfigDict(frozen=True)
    workflow: str | None = None
    direction: Direction | None = None
    setup_type: str | None = None
    symbol: str | None = None
    market_regime: str | None = None
    volatility_regime: str | None = None
    catalyst_type: str | None = None
    analyst_tags: tuple[str, ...] = ()
    horizon_tag: str | None = None
    relative_strength: str | None = None
    risk_pattern: str | None = None


class Lesson(BaseModel):
    model_config = ConfigDict(frozen=True)
    lesson_id: str = Field(default_factory=lambda: str(uuid4()))
    statement: str
    category: str = "methodology"
    pattern_key: str | None = None
    applicability: LessonContext = Field(default_factory=LessonContext)
    status: LessonStatus = LessonStatus.OBSERVED
    supporting_observation_ids: tuple[str, ...] = ()
    counter_observation_ids: tuple[str, ...] = ()
    observation_count: int = Field(default=0, ge=0)
    confidence: float = Field(default=0, ge=0, le=1)
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)
    validation_reason: str | None = None
    evidence_summary: str = ""

    @model_validator(mode="after")
    def lifecycle_requires_evidence(self) -> "Lesson":
        if self.status == LessonStatus.VALIDATED and self.observation_count < 5:
            raise ValueError("VALIDATED lessons require at least 5 observations")
        return self


class SpecialistFinding(BaseModel):
    model_config = ConfigDict(frozen=True)
    specialist: str
    timestamp: datetime = Field(default_factory=now_utc)
    subject: str = ""
    stance: str = "UNKNOWN"
    data_quality: str = "UNAVAILABLE"
    facts: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    conclusion: str | None = None
    contradictions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    metrics: dict[str, Decimal | str | None] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class ActionableSetup(BaseModel):
    subject: str
    direction: Direction
    rationale: str
    expected_value: Decimal | None = None
    quality_score: float | None = Field(default=None, ge=0, le=1)
    activation_state: ActivationState = ActivationState.NOT_ACTIVE_YET
    entry_condition: str | None = None


class AnalysisResult(BaseModel):
    run_id: str
    question: str = ""
    timestamp: datetime = Field(default_factory=now_utc)
    bottom_line: str
    facts: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    specialist_findings: list[SpecialistFinding] = Field(default_factory=list)
    agreements: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    what_would_change_view: list[str] = Field(default_factory=list)
    predictions: list[Prediction] = Field(default_factory=list)
    actionable_setups: list[ActionableSetup] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    learning_context: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    decision: str = "INSUFFICIENT_EVIDENCE"
    ranking_factors: dict[str, str] = Field(default_factory=dict)
    risk_verdict: str = "UNAVAILABLE"


class Evidence(BaseModel):
    evidence_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str | None = None
    source: str
    reference: str
    content: str
    observed_at: datetime = Field(default_factory=now_utc)
    subject: str = ""
    quality: str = "UNKNOWN"
    data_status: str = "UNAVAILABLE"
