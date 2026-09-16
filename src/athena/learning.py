"""Deterministic methodological lesson lifecycle."""
from __future__ import annotations

import json

from .models import Lesson, LessonContext, LessonStatus, Outcome, now_utc
from .store import Store


def _key(statement: str) -> str: return " ".join(statement.casefold().split())


def _pattern_key(category: str, pattern: str, context: LessonContext) -> str:
    scope = context.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    return json.dumps({"category": category.upper(), "pattern": pattern.upper(), "scope": scope}, sort_keys=True, separators=(",", ":"))


def lesson_applies(lesson: Lesson, current: LessonContext) -> tuple[float, str]:
    """A validated lesson applies only when every known scope selector matches."""
    if lesson.status != LessonStatus.VALIDATED:
        return 0.0, "lesson is not validated"
    scope = lesson.applicability.model_dump(exclude_none=True, exclude_defaults=True)
    if len(scope) < 2:
        return 0.0, "lesson scope is too broad for safe applicability"
    actual = current.model_dump(exclude_none=True, exclude_defaults=True)
    for field, expected in scope.items():
        candidate = actual.get(field)
        if field == "analyst_tags":
            if not set(expected).issubset(set(candidate or ())):
                return 0.0, f"{field} mismatch"
        elif candidate != expected:
            return 0.0, f"{field} mismatch"
    return min(1.0, len(scope) / 5), "matched " + ", ".join(sorted(scope))


class LessonEngine:
    def __init__(self, store: Store) -> None: self.store = store

    def observe(self, statement: str, category: str, outcome: Outcome, supports: bool, *, context: LessonContext | None = None, pattern: str | None = None) -> Lesson:
        structured = context is not None and pattern is not None
        key = _pattern_key(category, pattern, context) if structured else None
        existing = next((lesson for lesson in self.store.list_lessons() if (lesson.pattern_key == key if structured else lesson.pattern_key is None and _key(lesson.statement) == _key(statement) and lesson.category == category)), None)
        lesson = existing or Lesson(statement=statement, category=category, pattern_key=key, applicability=context or LessonContext(), validation_reason="First independent observation")
        if existing is None: self.store.save_lesson(lesson)
        if not self.store.save_observation(lesson.lesson_id, outcome, supports):
            return lesson
        support = lesson.supporting_observation_ids + ((outcome.prediction_id,) if supports else ())
        counter = lesson.counter_observation_ids + (() if supports else (outcome.prediction_id,))
        if len(counter) > len(support):
            status, reason = LessonStatus.REJECTED, "Independent counter-evidence exceeds support"
        elif len(support) >= 5 and len(support) > len(counter):
            status, reason = LessonStatus.VALIDATED, "At least five independent supporting predictions and more support than counter-evidence"
        elif len(support) + len(counter) == 1:
            status, reason = LessonStatus.OBSERVED, "First independent observation"
        else:
            status, reason = LessonStatus.VALIDATING, "Accumulating independent observations"
        updated = lesson.model_copy(update={"supporting_observation_ids": support, "counter_observation_ids": counter, "observation_count": len(support), "status": status, "confidence": len(support) / (len(support) + len(counter)), "updated_at": now_utc(), "validation_reason": reason})
        self.store.save_lesson(updated)
        self.store.audit("lesson_observed", {"lesson_id": lesson.lesson_id, "prediction_id": outcome.prediction_id, "outcome_id": outcome.outcome_id, "supports": supports})
        if status != lesson.status:
            self.store.audit("lesson_transition", {"lesson_id": lesson.lesson_id, "from": lesson.status.value, "to": status.value, "reason": reason})
        return updated
