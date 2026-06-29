"""
SQLite audit trail — logs every surgical update and chatbot interaction.

Two tables:
  sessions      — one row per one-pager generation session
  audit_entries — one row per event (surgical_update or chat_interaction)

For surgical updates, each SectionUpdate is stored as a separate row with
before/after/rationale/evidence, plus the user's prompt (new info) and the
model's reasoning steps for debugging.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.schema_onepager import OnePagerData

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "audit.db"


def _ensure_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            company_name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            model_name TEXT NOT NULL,
            doc_filename TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK(event_type IN ('surgical_update', 'chat_interaction')),
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),

            -- trigger: what the user provided
            user_prompt TEXT,

            -- surgical update fields
            section_name TEXT,
            field_path TEXT,
            before_text TEXT,
            after_text TEXT,
            rationale TEXT,
            evidence TEXT,
            accepted INTEGER DEFAULT 0,

            -- chat interaction fields
            model_response TEXT,
            context_snapshot TEXT,

            -- debug / reasoning
            model_name TEXT,
            model_reasoning TEXT,
            metadata_json TEXT,

            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    """)
    conn.commit()
    return conn


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_onepager(op: OnePagerData) -> str:
    try:
        return op.model_dump_json(indent=2)
    except Exception:
        return str(op)


# ── Session management ──────────────────────────────────────────────────────

def create_session(company_name: str, model_name: str, doc_filename: str = "") -> str:
    session_id = uuid.uuid4().hex[:12]
    conn = _ensure_db()
    conn.execute(
        "INSERT INTO sessions (id, company_name, model_name, doc_filename, created_at) VALUES (?, ?, ?, ?, ?)",
        (session_id, company_name, model_name, doc_filename, _utcnow()),
    )
    conn.commit()
    conn.close()
    return session_id


def get_session(session_id: str) -> Optional[dict]:
    conn = _ensure_db()
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    conn.close()
    if row:
        cols = ["id", "company_name", "created_at", "model_name", "doc_filename"]
        return dict(zip(cols, row))
    return None


def list_sessions(limit: int = 20) -> list[dict]:
    conn = _ensure_db()
    rows = conn.execute(
        "SELECT id, company_name, created_at, model_name, doc_filename FROM sessions ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(zip(["id", "company_name", "created_at", "model_name", "doc_filename"], r)) for r in rows]


# ── Entry logging ───────────────────────────────────────────────────────────

def log_surgical_update(
    session_id: str,
    section_name: str,
    field_path: str,
    before_text: str,
    after_text: str,
    rationale: str,
    evidence: str,
    accepted: bool,
    user_prompt: str,
    model_name: str = "",
    model_reasoning: str = "",
    metadata: Optional[dict] = None,
) -> int:
    conn = _ensure_db()
    cur = conn.execute(
        """INSERT INTO audit_entries
           (session_id, event_type, timestamp, user_prompt,
            section_name, field_path, before_text, after_text, rationale, evidence, accepted,
            model_name, model_reasoning, metadata_json)
           VALUES (?, 'surgical_update', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id, _utcnow(), user_prompt,
            section_name, field_path, before_text, after_text, rationale, evidence,
            1 if accepted else 0,
            model_name, model_reasoning,
            json.dumps(metadata) if metadata else None,
        ),
    )
    conn.commit()
    entry_id = cur.lastrowid
    conn.close()
    return entry_id


def log_chat_interaction(
    session_id: str,
    user_prompt: str,
    model_response: str,
    context_onepager: Optional[OnePagerData] = None,
    model_name: str = "",
    model_reasoning: str = "",
    metadata: Optional[dict] = None,
) -> int:
    conn = _ensure_db()
    context_snapshot = _serialize_onepager(context_onepager) if context_onepager else None
    cur = conn.execute(
        """INSERT INTO audit_entries
           (session_id, event_type, timestamp, user_prompt,
            model_response, context_snapshot,
            model_name, model_reasoning, metadata_json)
           VALUES (?, 'chat_interaction', ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id, _utcnow(), user_prompt,
            model_response, context_snapshot,
            model_name, model_reasoning,
            json.dumps(metadata) if metadata else None,
        ),
    )
    conn.commit()
    entry_id = cur.lastrowid
    conn.close()
    return entry_id


def log_chat_interaction_batch(
    session_id: str,
    user_msg: str,
    assistant_msg: str,
    context_onepager: Optional[OnePagerData] = None,
    model_name: str = "",
) -> None:
    log_chat_interaction(
        session_id=session_id,
        user_prompt=user_msg,
        model_response=assistant_msg,
        context_onepager=context_onepager,
        model_name=model_name,
    )


# ── Query helpers ───────────────────────────────────────────────────────────

def get_entries_for_session(session_id: str, event_type: Optional[str] = None) -> list[dict]:
    conn = _ensure_db()
    conn.row_factory = sqlite3.Row
    if event_type:
        rows = conn.execute(
            "SELECT * FROM audit_entries WHERE session_id = ? AND event_type = ? ORDER BY timestamp ASC",
            (session_id, event_type),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM audit_entries WHERE session_id = ? ORDER BY timestamp ASC",
            (session_id,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_entry(entry_id: int) -> Optional[dict]:
    conn = _ensure_db()
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM audit_entries WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_entry_accepted(entry_id: int, accepted: bool) -> None:
    conn = _ensure_db()
    conn.execute("UPDATE audit_entries SET accepted = ? WHERE id = ?", (1 if accepted else 0, entry_id))
    conn.commit()
    conn.close()


def get_stats_for_session(session_id: str) -> dict[str, int]:
    conn = _ensure_db()
    surgical = conn.execute(
        "SELECT COUNT(*) FROM audit_entries WHERE session_id = ? AND event_type = 'surgical_update'",
        (session_id,),
    ).fetchone()[0]
    chat = conn.execute(
        "SELECT COUNT(*) FROM audit_entries WHERE session_id = ? AND event_type = 'chat_interaction'",
        (session_id,),
    ).fetchone()[0]
    accepted = conn.execute(
        "SELECT COUNT(*) FROM audit_entries WHERE session_id = ? AND event_type = 'surgical_update' AND accepted = 1",
        (session_id,),
    ).fetchone()[0]
    conn.close()
    return {"surgical_updates": surgical, "chat_interactions": chat, "accepted_updates": accepted}


def delete_session(session_id: str) -> None:
    conn = _ensure_db()
    conn.execute("DELETE FROM audit_entries WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()
