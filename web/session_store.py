"""SQLite-backed session storage for deliberation and analysis sessions."""

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path


class SessionStore:
    """Persistent session storage using SQLite."""

    def __init__(self, db_path: str = "data/sessions.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT DEFAULT (datetime('now')),
                    pipeline_type TEXT NOT NULL DEFAULT 'deliberate',
                    question TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'council',
                    depth TEXT DEFAULT 'basic',
                    length TEXT DEFAULT 'standard',
                    status TEXT DEFAULT 'pending',
                    progress_step TEXT,
                    result_html TEXT,
                    result_json TEXT,
                    total_cost REAL,
                    duration_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS session_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT REFERENCES sessions(id),
                    timestamp TEXT DEFAULT (datetime('now')),
                    event_type TEXT,
                    data TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_created
                    ON sessions(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_events_session
                    ON session_events(session_id, timestamp);
            """)
            conn.commit()
        finally:
            conn.close()

    def create_session(self, pipeline_type: str, question: str, mode: str = "council",
                       depth: str = "basic", length: str = "standard") -> str:
        session_id = uuid.uuid4().hex[:12]
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO sessions (id, pipeline_type, question, mode, depth, length) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, pipeline_type, question, mode, depth, length),
            )
            conn.commit()
        finally:
            conn.close()
        return session_id

    def update_status(self, session_id: str, status: str, progress_step: str = None) -> None:
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE sessions SET status = ?, progress_step = ? WHERE id = ?",
                (status, progress_step, session_id),
            )
            conn.commit()
        finally:
            conn.close()

    def add_event(self, session_id: str, event_type: str, data: dict = None) -> None:
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO session_events (session_id, event_type, data) VALUES (?, ?, ?)",
                (session_id, event_type, json.dumps(data) if data else None),
            )
            conn.commit()
        finally:
            conn.close()

    def store_result(self, session_id: str, result_html: str, result_json: dict,
                     total_cost: float = None, duration_ms: int = None) -> None:
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE sessions
                   SET status = 'complete', result_html = ?, result_json = ?,
                       total_cost = ?, duration_ms = ?
                   WHERE id = ?""",
                (result_html, json.dumps(result_json), total_cost, duration_ms, session_id),
            )
            conn.commit()
        finally:
            conn.close()

    def store_error(self, session_id: str, error: str) -> None:
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE sessions SET status = 'error', progress_step = ? WHERE id = ?",
                (error, session_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_session(self, session_id: str) -> dict | None:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row:
                result = dict(row)
                if result.get("result_json"):
                    result["result_json"] = json.loads(result["result_json"])
                return result
            return None
        finally:
            conn.close()

    def get_events(self, session_id: str) -> list[dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM session_events WHERE session_id = ? ORDER BY timestamp",
                (session_id,),
            ).fetchall()
            events = []
            for row in rows:
                event = dict(row)
                if event.get("data"):
                    event["data"] = json.loads(event["data"])
                events.append(event)
            return events
        finally:
            conn.close()

    def list_sessions(self, limit: int = 50, offset: int = 0) -> list[dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT id, created_at, pipeline_type, question, mode, depth, length,
                          status, duration_ms, total_cost
                   FROM sessions ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
