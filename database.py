"""
database.py - SQLite persistence layer.

Tables:
  jobs             – one row per unique (company, role) pair
  processed_emails – Gmail message IDs to prevent double-processing
  meta             – key/value store (checkpoint timestamp, etc.)
"""

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Generator, Optional

from config import DB_PATH, STATUS_PRIORITY

logger = logging.getLogger(__name__)


@dataclass
class Job:
    id: Optional[int]
    company: str
    role: str
    status: str
    applied_date: str
    last_updated: str


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def _normalise(text: str) -> str:
    return text.lower().strip()


class Database:
    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db_path = db_path
        self._init_schema()

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    company      TEXT    NOT NULL,
                    role         TEXT    NOT NULL,
                    status       TEXT    NOT NULL DEFAULT 'Applied',
                    applied_date TEXT    NOT NULL,
                    last_updated TEXT    NOT NULL,
                    UNIQUE(company, role) ON CONFLICT IGNORE
                );

                CREATE TABLE IF NOT EXISTS processed_emails (
                    gmail_message_id TEXT PRIMARY KEY,
                    processed_at     TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_company_role
                    ON jobs(company, role);
            """)

    # ── Meta / checkpoint ──────────────────────────────────────────────────────

    def get_meta(self, key: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_checkpoint(self) -> Optional[str]:
        """Return ISO-8601 timestamp of the last processed email, or None."""
        return self.get_meta("last_email_at")

    def set_checkpoint(self, iso_ts: str) -> None:
        self.set_meta("last_email_at", iso_ts)

    # ── processed_emails ──────────────────────────────────────────────────────

    def is_processed(self, gmail_message_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM processed_emails WHERE gmail_message_id=?",
                (gmail_message_id,),
            ).fetchone()
            return row is not None

    def mark_processed(self, gmail_message_id: str, email_date: Optional[str] = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO processed_emails(gmail_message_id, processed_at) "
                "VALUES(?,?)",
                (gmail_message_id, _now_utc()),
            )
        # Advance checkpoint to the latest email date seen
        if email_date:
            current = self.get_checkpoint()
            if current is None or email_date > current:
                self.set_checkpoint(email_date)

    # ── jobs ──────────────────────────────────────────────────────────────────

    def find_job(self, company: str, role: str) -> Optional[Job]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE lower(company)=? AND lower(role)=?",
                (_normalise(company), _normalise(role)),
            ).fetchone()
            return Job(**dict(row)) if row else None

    def upsert_job(
        self, company: str, role: str, status: str, applied_date: str
    ) -> tuple[str, Job]:
        now = _now_utc()
        existing = self.find_job(company, role)

        if existing is None:
            with self._conn() as conn:
                cur = conn.execute(
                    "INSERT INTO jobs(company,role,status,applied_date,last_updated) "
                    "VALUES(?,?,?,?,?)",
                    (company, role, status, applied_date, now),
                )
                job = Job(
                    id=cur.lastrowid, company=company, role=role,
                    status=status, applied_date=applied_date, last_updated=now,
                )
            logger.info("Created: %s @ %s [%s]", role, company, status)
            return "created", job

        new_p = STATUS_PRIORITY.get(status, 0)
        cur_p = STATUS_PRIORITY.get(existing.status, 0)

        if new_p >= cur_p:
            with self._conn() as conn:
                conn.execute(
                    "UPDATE jobs SET status=?, last_updated=? WHERE id=?",
                    (status, now, existing.id),
                )
            existing.status = status
            existing.last_updated = now
            logger.info("Updated: %s @ %s → %s", existing.role, existing.company, status)
            return "updated", existing

        return "unchanged", existing

    def get_job(self, job_id: int) -> Optional[Job]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return Job(**dict(row)) if row else None

    def update_job(self, job_id: int, company: str, role: str, status: str, applied_date: str) -> Optional[Job]:
        now = _now_utc()
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET company=?,role=?,status=?,applied_date=?,last_updated=? WHERE id=?",
                (company, role, status, applied_date, now, job_id),
            )
        return self.get_job(job_id)

    def delete_job(self, job_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            return cur.rowcount > 0

    def insert_job(self, company: str, role: str, status: str, applied_date: str) -> Job:
        now = _now_utc()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO jobs(company,role,status,applied_date,last_updated) VALUES(?,?,?,?,?)",
                (company, role, status, applied_date, now),
            )
            return Job(
                id=cur.lastrowid, company=company, role=role,
                status=status, applied_date=applied_date, last_updated=now,
            )

    def all_jobs(self) -> list[Job]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY applied_date DESC, last_updated DESC"
            ).fetchall()
            return [Job(**dict(r)) for r in rows]

    def stats(self) -> dict[str, int]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as n FROM jobs GROUP BY status"
            ).fetchall()
            return {r["status"]: r["n"] for r in rows}
        

    def clear_processed_emails(self) -> None:
        """Clear the processed_emails table to allow re-processing."""
        with self._conn() as conn:
            conn.execute("DELETE FROM processed_emails")
        logger.info("Cleared processed_emails table")