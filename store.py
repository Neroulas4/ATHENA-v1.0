import json, sqlite3, uuid
from pathlib import Path
from .models import Prediction, Outcome, Lesson

class Store:
    def __init__(self, path=None):
        import os
        path = path or os.getenv("ATHENA_DB_PATH", "data/athena.db")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY, kind TEXT, question TEXT, started_at TEXT,
            completed_at TEXT, status TEXT, output_json TEXT, trace_id TEXT
        );
        CREATE TABLE IF NOT EXISTS predictions (
            prediction_id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS outcomes (
            id TEXT PRIMARY KEY, prediction_id TEXT NOT NULL, payload TEXT NOT NULL,
            evaluated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS lessons (
            lesson_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS audit (
            id TEXT PRIMARY KEY, event TEXT, payload TEXT, created_at TEXT
        );
        """)
        self.db.commit()

    def start_run(self, kind, question, trace_id=None):
        rid = str(uuid.uuid4())
        self.db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)",
                        (rid, kind, question, __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), None, "RUNNING", None, trace_id))
        self.db.commit()
        return rid

    def finish_run(self, run_id, result, status="COMPLETED"):
        self.db.execute("UPDATE runs SET completed_at=datetime('now'),status=?,output_json=? WHERE run_id=?",
                        (status, result.model_dump_json(), run_id))
        self.db.commit()

    def save_prediction(self, p: Prediction):
        self.db.execute("INSERT OR REPLACE INTO predictions VALUES (?,?,?)",
                        (p.prediction_id, p.model_dump_json(), p.timestamp.isoformat()))
        self.db.commit()

    def save_outcome(self, o: Outcome):
        self.db.execute("INSERT INTO outcomes VALUES (?,?,?,?)",
                        (str(uuid.uuid4()), o.prediction_id, o.model_dump_json(), o.evaluated_at.isoformat()))
        self.db.commit()

    def save_lesson(self, l: Lesson):
        self.db.execute("INSERT OR REPLACE INTO lessons VALUES (?,?,?)",
                        (l.lesson_id, l.model_dump_json(), l.created_at.isoformat()))
        self.db.commit()

    def recent_predictions(self, limit=20):
        rows = self.db.execute("SELECT payload FROM predictions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [Prediction.model_validate_json(r[0]) for r in rows]

    def audit(self, event, payload):
        self.db.execute("INSERT INTO audit VALUES (?,?,?,datetime('now'))",
                        (str(uuid.uuid4()), event, json.dumps(payload, default=str)))
        self.db.commit()
