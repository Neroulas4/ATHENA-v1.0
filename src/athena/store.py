"""Persistence abstraction: SQLite locally, PostgreSQL when DATABASE_URL is set."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import AnalysisResult, Evidence, Lesson, Outcome, Prediction, SpecialistFinding, now_utc


class Store:
    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or os.getenv("DATABASE_URL")
        self.is_postgres = bool(self.database_url and self.database_url.startswith(("postgres://", "postgresql://")))
        if self.is_postgres:
            try:
                import psycopg
            except ImportError as exc:  # pragma: no cover - deployment guard
                raise RuntimeError("PostgreSQL requires psycopg; install project dependencies") from exc
            self.db = psycopg.connect(self.database_url)
            self.placeholder = "%s"
        else:
            path = database_url or os.getenv("ATHENA_DB_PATH", "data/athena.db")
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(path)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.placeholder = "?"
        self._migrate()

    def _execute(self, query: str, params: tuple[Any, ...] = ()):
        if self.is_postgres:
            query = query.replace("?", "%s")
        return self.db.execute(query, params)

    def _migrate(self) -> None:
        serial = "TEXT" if not self.is_postgres else "TEXT"
        schema = """
        CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, kind TEXT NOT NULL, question TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT, status TEXT NOT NULL, output_json TEXT, trace_id TEXT);
        CREATE TABLE IF NOT EXISTS predictions (prediction_id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS outcomes (outcome_id TEXT PRIMARY KEY, prediction_id TEXT NOT NULL REFERENCES predictions(prediction_id), payload TEXT NOT NULL, evaluated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS lessons (lesson_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit_events (event_id TEXT PRIMARY KEY, event TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS evidence (evidence_id TEXT PRIMARY KEY, run_id TEXT REFERENCES runs(run_id), payload TEXT NOT NULL, observed_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workflow_locks (lock_key TEXT PRIMARY KEY, acquired_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS analyst_findings (finding_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), specialist TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS lesson_observations (lesson_id TEXT NOT NULL REFERENCES lessons(lesson_id), prediction_id TEXT NOT NULL, outcome_id TEXT NOT NULL, supports INTEGER NOT NULL, PRIMARY KEY(lesson_id,prediction_id));
        CREATE TABLE IF NOT EXISTS workflow_idempotency (idempotency_key TEXT PRIMARY KEY, run_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS delivery_claims (idempotency_key TEXT PRIMARY KEY, run_id TEXT NOT NULL, status TEXT NOT NULL, retry_count INTEGER NOT NULL, attempted_at TEXT NOT NULL, retryable INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS delivery_attempts (delivery_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL, run_id TEXT NOT NULL, payload TEXT NOT NULL, attempted_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_delivery_run ON delivery_attempts(run_id, attempted_at);
        CREATE TABLE IF NOT EXISTS api_requests (request_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response_json TEXT, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_predictions_created ON predictions(created_at);
        CREATE INDEX IF NOT EXISTS idx_outcomes_prediction ON outcomes(prediction_id, evaluated_at);
        CREATE INDEX IF NOT EXISTS idx_runs_kind_started ON runs(kind, started_at);
        CREATE INDEX IF NOT EXISTS idx_findings_run ON analyst_findings(run_id);
        """
        if self.is_postgres:
            for statement in schema.split(";"):
                if statement.strip(): self._execute(statement)
        else:
            self.db.executescript(schema)
        self.db.commit()

    def start_run(self, kind: str, question: str, trace_id: str | None = None) -> str:
        run_id = str(uuid4())
        self._execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)", (run_id, kind, question, now_utc().isoformat(), None, "RUNNING", None, trace_id))
        self.db.commit(); return run_id

    def finish_run(self, run_id: str, result: AnalysisResult, status: str = "COMPLETED") -> None:
        self._execute("UPDATE runs SET completed_at=?, status=?, output_json=? WHERE run_id=?", (now_utc().isoformat(), status, result.model_dump_json(), run_id))
        self.db.commit()

    def save_prediction(self, prediction: Prediction) -> None:
        # INSERT, never replace/update: predictions are historical snapshots.
        self._execute("INSERT INTO predictions VALUES (?,?,?)", (prediction.prediction_id, prediction.model_dump_json(), prediction.timestamp.isoformat()))
        self.db.commit()

    def get_prediction(self, prediction_id: str) -> Prediction | None:
        row = self._execute("SELECT payload FROM predictions WHERE prediction_id=?", (prediction_id,)).fetchone()
        return Prediction.model_validate_json(row[0]) if row else None

    def recent_predictions(self, limit: int = 100) -> list[Prediction]:
        rows = self._execute("SELECT payload FROM predictions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [Prediction.model_validate_json(row[0]) for row in rows]

    def save_outcome(self, outcome: Outcome) -> None:
        if not self.get_prediction(outcome.prediction_id):
            raise ValueError("Outcome references an unknown prediction")
        self._execute("INSERT INTO outcomes VALUES (?,?,?,?)", (outcome.outcome_id, outcome.prediction_id, outcome.model_dump_json(), outcome.evaluated_at.isoformat()))
        self.db.commit()

    def outcomes_for(self, prediction_id: str) -> list[Outcome]:
        rows = self._execute("SELECT payload FROM outcomes WHERE prediction_id=? ORDER BY evaluated_at", (prediction_id,)).fetchall()
        return [Outcome.model_validate_json(row[0]) for row in rows]

    def get_outcome(self, outcome_id: str) -> Outcome | None:
        row = self._execute("SELECT payload FROM outcomes WHERE outcome_id=?", (outcome_id,)).fetchone()
        return Outcome.model_validate_json(row[0]) if row else None

    def list_outcomes(self, limit: int = 200) -> list[Outcome]:
        rows = self._execute("SELECT payload FROM outcomes ORDER BY evaluated_at DESC LIMIT ?", (limit,)).fetchall()
        return [Outcome.model_validate_json(row[0]) for row in rows]

    def latest_outcome(self, prediction_id: str) -> Outcome | None:
        values = self.outcomes_for(prediction_id)
        return values[-1] if values else None

    def eligible_predictions(self, limit: int = 200) -> list[Prediction]:
        """Pending and untriggered predictions can be reevaluated; completed results are preserved."""
        eligible = []
        for prediction in self.recent_predictions(limit):
            latest = self.latest_outcome(prediction.prediction_id)
            if latest is None or latest.outcome_status in {"PENDING", "UNTRIGGERED"}:
                eligible.append(prediction)
        return eligible

    def save_lesson(self, lesson: Lesson) -> None:
        existing = self._execute("SELECT lesson_id FROM lessons WHERE lesson_id=?", (lesson.lesson_id,)).fetchone()
        if existing:
            self._execute("UPDATE lessons SET payload=?, updated_at=? WHERE lesson_id=?", (lesson.model_dump_json(), lesson.updated_at.isoformat(), lesson.lesson_id))
        else:
            self._execute("INSERT INTO lessons VALUES (?,?,?)", (lesson.lesson_id, lesson.model_dump_json(), lesson.updated_at.isoformat()))
        self.db.commit()

    def validated_lessons(self) -> list[Lesson]:
        rows = self._execute("SELECT payload FROM lessons").fetchall()
        return [lesson for row in rows if (lesson := Lesson.model_validate_json(row[0])).status.value == "VALIDATED"]

    def list_lessons(self) -> list[Lesson]:
        return [Lesson.model_validate_json(row[0]) for row in self._execute("SELECT payload FROM lessons ORDER BY updated_at DESC").fetchall()]

    def candidate_lessons(self) -> list[Lesson]:
        return [lesson for lesson in self.list_lessons() if lesson.status.value in {"OBSERVED", "VALIDATING"}]

    def recent_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._execute("SELECT run_id,kind,question,started_at,completed_at,status FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) if not self.is_postgres else dict(zip(("run_id", "kind", "question", "started_at", "completed_at", "status"), row)) for row in rows]

    def get_run_result(self, run_id: str) -> AnalysisResult | None:
        row = self._execute("SELECT output_json FROM runs WHERE run_id=? AND status='COMPLETED'", (run_id,)).fetchone()
        return AnalysisResult.model_validate_json(row[0]) if row and row[0] else None

    def latest_run_result(self, kind: str) -> AnalysisResult | None:
        row = self._execute("SELECT output_json FROM runs WHERE kind=? AND status='COMPLETED' ORDER BY started_at DESC LIMIT 1", (kind,)).fetchone()
        return AnalysisResult.model_validate_json(row[0]) if row and row[0] else None

    def scheduled_run_id(self, key: str) -> str | None:
        row = self._execute("SELECT run_id FROM workflow_idempotency WHERE idempotency_key=?", (key,)).fetchone()
        return row[0] if row and row[0] != "CLAIMED" else None

    def claim_delivery(self, key: str, run_id: str, *, max_attempts: int = 3) -> int | None:
        """Claim one send attempt; a stale uncertain send is retried only within provider's 24h key window."""
        from datetime import datetime, timezone
        current = now_utc()
        try:
            self._execute("INSERT INTO delivery_claims VALUES (?,?,?,?,?,?)", (key, run_id, "PENDING", 0, current.isoformat(), 1))
            self.db.commit()
            return 0
        except Exception:
            self.db.rollback()
        row = self._execute("SELECT status,retry_count,attempted_at,retryable FROM delivery_claims WHERE idempotency_key=? AND run_id=?", (key, run_id)).fetchone()
        if not row or row[0] in {"SENT", "NOT_CONFIGURED", "SKIPPED"} or not row[3] or row[1] >= max_attempts - 1:
            return None
        age = current - datetime.fromisoformat(row[2]).astimezone(timezone.utc)
        if row[0] == "PENDING" and (age.total_seconds() < 300 or age.total_seconds() >= 86400):
            return None
        updated = self._execute("UPDATE delivery_claims SET status='PENDING',retry_count=retry_count+1,attempted_at=? WHERE idempotency_key=? AND status=? AND retry_count=?", (current.isoformat(), key, row[0], row[1]))
        self.db.commit()
        return row[1] + 1 if updated.rowcount == 1 else None

    def record_delivery(self, delivery) -> None:
        self._execute("INSERT INTO delivery_attempts VALUES (?,?,?,?,?)", (delivery.delivery_id, delivery.idempotency_key, delivery.run_id, delivery.model_dump_json(), delivery.attempted_at.isoformat()))
        self._execute("UPDATE delivery_claims SET status=?,retryable=? WHERE idempotency_key=?", (delivery.status, 1 if delivery.retryable else 0, delivery.idempotency_key))
        self.db.commit()

    def deliveries_for_run(self, run_id: str):
        from .delivery import DeliveryAttempt
        rows = self._execute("SELECT payload FROM delivery_attempts WHERE run_id=? ORDER BY attempted_at", (run_id,)).fetchall()
        return [DeliveryAttempt.model_validate_json(row[0]) for row in rows]

    def claim_api_request(self, key: str, fingerprint: str) -> tuple[str, dict[str, Any] | None]:
        try:
            self._execute("INSERT INTO api_requests VALUES (?,?,?,?)", (key, fingerprint, None, now_utc().isoformat()))
            self.db.commit()
            return "CLAIMED", None
        except Exception:
            self.db.rollback()
        row = self._execute("SELECT fingerprint,response_json FROM api_requests WHERE request_key=?", (key,)).fetchone()
        if not row or row[0] != fingerprint: return "CONFLICT", None
        return ("COMPLETE", json.loads(row[1])) if row[1] else ("IN_PROGRESS", None)

    def complete_api_request(self, key: str, response: dict[str, Any]) -> None:
        self._execute("UPDATE api_requests SET response_json=? WHERE request_key=?", (json.dumps(response, default=str), key))
        self.db.commit()

    def release_api_request(self, key: str) -> None:
        self._execute("DELETE FROM api_requests WHERE request_key=? AND response_json IS NULL", (key,))
        self.db.commit()

    def acquire_lock(self, lock_key: str) -> bool:
        try:
            expired_before = (now_utc() - timedelta(hours=6)).isoformat()
            self._execute("DELETE FROM workflow_locks WHERE lock_key=? AND acquired_at<?", (lock_key, expired_before))
            self._execute("INSERT INTO workflow_locks VALUES (?,?)", (lock_key, now_utc().isoformat()))
            self.db.commit(); return True
        except Exception:
            self.db.rollback(); return False

    def release_lock(self, lock_key: str) -> None:
        self._execute("DELETE FROM workflow_locks WHERE lock_key=?", (lock_key,)); self.db.commit()

    def claim_schedule(self, key: str) -> bool:
        try:
            self._execute("INSERT INTO workflow_idempotency VALUES (?,?)", (key, "CLAIMED"))
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            return False

    def complete_schedule(self, key: str, run_id: str) -> None:
        self._execute("UPDATE workflow_idempotency SET run_id=? WHERE idempotency_key=?", (run_id, key))
        self.db.commit()

    def release_schedule(self, key: str) -> None:
        self._execute("DELETE FROM workflow_idempotency WHERE idempotency_key=?", (key,))
        self.db.commit()

    def save_evidence(self, evidence: Evidence) -> None:
        self._execute("INSERT INTO evidence VALUES (?,?,?,?)", (evidence.evidence_id, evidence.run_id, evidence.model_dump_json(), evidence.observed_at.isoformat()))
        self.db.commit()

    def save_finding(self, run_id: str, finding: SpecialistFinding) -> None:
        self._execute("INSERT INTO analyst_findings VALUES (?,?,?,?)", (str(uuid4()), run_id, finding.specialist, finding.model_dump_json()))
        self.db.commit()

    def findings_for_run(self, run_id: str) -> list[SpecialistFinding]:
        rows = self._execute("SELECT payload FROM analyst_findings WHERE run_id=?", (run_id,)).fetchall()
        return [SpecialistFinding.model_validate_json(row[0]) for row in rows]

    def evidence_for_run(self, run_id: str) -> list[Evidence]:
        rows = self._execute("SELECT payload FROM evidence WHERE run_id=?", (run_id,)).fetchall()
        return [Evidence.model_validate_json(row[0]) for row in rows]

    def observation_for(self, lesson_id: str, prediction_id: str) -> str | None:
        row = self._execute("SELECT outcome_id FROM lesson_observations WHERE lesson_id=? AND prediction_id=?", (lesson_id, prediction_id)).fetchone()
        return row[0] if row else None

    def save_observation(self, lesson_id: str, outcome: Outcome, supports: bool) -> bool:
        if self.observation_for(lesson_id, outcome.prediction_id): return False
        self._execute("INSERT INTO lesson_observations VALUES (?,?,?,?)", (lesson_id, outcome.prediction_id, outcome.outcome_id, 1 if supports else 0))
        self.db.commit()
        return True

    def audit(self, event: str, payload: dict[str, Any]) -> None:
        self._execute("INSERT INTO audit_events VALUES (?,?,?,?)", (str(uuid4()), event, json.dumps(payload, default=str), now_utc().isoformat()))
        self.db.commit()

    def post_mortem_review(self, run_id: str) -> dict[str, Any] | None:
        rows = self._execute("SELECT payload FROM audit_events WHERE event='post_mortem_review' ORDER BY created_at DESC").fetchall()
        return next((item for row in rows if (item := json.loads(row[0])).get("run_id") == run_id), None)

    def close(self) -> None:
        self.db.close()
