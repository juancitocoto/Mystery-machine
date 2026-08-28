"""
jobs.py
-------
Tracks the status of each mystery-shop report job, persisted to a local
SQLite database file (see JOBS_DB_PATH in config.py). SQLite ships with
Python - no extra service to install or run - but unlike the original
in-memory dict, this survives a server restart: job history isn't lost
just because you re-deployed or the process crashed.

Nothing outside this file changed - main.py and worker.py still just
call create_job() / get_job() / update_job(), exactly as before. That's
the point of keeping storage logic behind these three functions: when
you're ready to grow further (e.g. running multiple server processes
that all need to see the same jobs), swap this out for Postgres - same
three functions, different connection.
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import JOBS_DB_PATH
from app.models import ShopJobResponse, JobStatus


def _get_connection() -> sqlite3.Connection:
    """
    Opens a brand new connection for each call rather than keeping one
    connection open for the app's whole lifetime.

    Why: SQLite connections aren't safe to share across threads by
    default, and FastAPI can run request handling on different threads.
    Opening a short-lived connection per operation sidesteps that
    entirely - simple, and plenty fast for SQLite at this scale.
    """
    conn = sqlite3.connect(JOBS_DB_PATH)
    conn.row_factory = sqlite3.Row  # lets us read columns by name, e.g. row["status"]
    return conn


def _init_db() -> None:
    """Creates the jobs table if it doesn't already exist. Runs once, at import time."""
    db_dir = Path(JOBS_DB_PATH).parent
    if str(db_dir) not in ("", "."):
        db_dir.mkdir(parents=True, exist_ok=True)

    with _get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id        TEXT PRIMARY KEY,
                status        TEXT NOT NULL,
                report        TEXT,
                error_message TEXT
            )
            """
        )
        # Lightweight migration: adds the consent-audit-trail columns to
        # an existing jobs.db from before they existed, instead of
        # requiring a fresh database. New columns come in nullable
        # (SQLite can't add a NOT NULL column without a default to a
        # table that might already have rows) - every job created going
        # forward always fills them in regardless.
        _ensure_column(conn, "shop_state", "TEXT")
        _ensure_column(conn, "consent_requirement", "TEXT")
        _ensure_column(conn, "consent_attested", "INTEGER")
        _ensure_column(conn, "employer_disclosure_attested", "INTEGER")
        # Added for the support agent (see app/support_agent.py) - without
        # a timestamp, a question like "how many shops this month" has no
        # way to be answered. NULL on legacy pre-migration rows.
        _ensure_column(conn, "created_at", "TEXT")


def _ensure_column(conn: sqlite3.Connection, column: str, sql_type: str) -> None:
    """Adds `column` to the jobs table if it doesn't already exist."""
    existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    if column not in existing_columns:
        conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {sql_type}")


_init_db()


def _row_to_job(row: sqlite3.Row) -> ShopJobResponse:
    """Converts one SQLite row back into our ShopJobResponse model."""
    return ShopJobResponse(
        job_id=row["job_id"],
        status=JobStatus(row["status"]),
        report=row["report"],
        error_message=row["error_message"],
        shop_state=row["shop_state"],
        consent_requirement=row["consent_requirement"],
        # SQLite has no native bool type - these are stored as 0/1
        # integers (or NULL for a legacy pre-migration row).
        consent_attested=bool(row["consent_attested"]) if row["consent_attested"] is not None else None,
        employer_disclosure_attested=(
            bool(row["employer_disclosure_attested"]) if row["employer_disclosure_attested"] is not None else None
        ),
        created_at=row["created_at"],
    )


def create_job(
    shop_state: str,
    consent_requirement: str,
    consent_attested: bool,
    employer_disclosure_attested: bool,
) -> ShopJobResponse:
    """
    Create a new job in PENDING state and store it, along with the
    recording-consent attestations that justified accepting it (see
    app/consent_law.py) - this is the audit trail for why this
    particular recording was allowed through.
    """
    job = ShopJobResponse(
        job_id=uuid.uuid4().hex,
        status=JobStatus.PENDING,
        shop_state=shop_state,
        consent_requirement=consent_requirement,
        consent_attested=consent_attested,
        employer_disclosure_attested=employer_disclosure_attested,
        # UTC, ISO 8601 (e.g. "2026-08-27T14:03:11.123456+00:00") - stored
        # as plain TEXT since SQLite has no native datetime type. UTC (not
        # local time) so it sorts/compares correctly regardless of where
        # the server runs.
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    with _get_connection() as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, status, report, error_message,
                shop_state, consent_requirement, consent_attested, employer_disclosure_attested,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.job_id,
                job.status.value,
                job.report,
                job.error_message,
                job.shop_state,
                job.consent_requirement,
                int(job.consent_attested),
                int(job.employer_disclosure_attested),
                job.created_at,
            ),
        )
    return job


def get_job(job_id: str) -> Optional[ShopJobResponse]:
    """Look up a job by ID. Returns None if it doesn't exist."""
    with _get_connection() as conn:
        row = conn.execute(
            """
            SELECT job_id, status, report, error_message,
                   shop_state, consent_requirement, consent_attested, employer_disclosure_attested,
                   created_at
            FROM jobs WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
    return _row_to_job(row) if row is not None else None


def run_read_only_query(sql: str) -> list:
    """
    Runs a single SELECT against the jobs database and returns the rows
    as a list of dicts (column name -> value).

    Used by the support agent's query tool (app/support_agent.py). The
    connection is opened in SQLite's own read-only mode ("mode=ro" in the
    URI below) - not just checked for a leading "SELECT" in the text -
    so even a cleverly-crafted query (e.g. one hidden inside a comment or
    using an unexpected SQLite feature) physically cannot write to the
    database through this connection; SQLite itself rejects the write.
    That's a stronger guarantee than text-checking alone, which is why
    support_agent.py's text check and this read-only connection are both
    in place rather than relying on just one of them.

    Raises sqlite3.Error (e.g. sqlite3.OperationalError) if the query is
    invalid or - because the connection is read-only - if it attempts to
    write; the caller (support_agent.py) turns that into a message for
    Claude to see and react to, not a crash.
    """
    # Path.resolve().as_uri() reliably builds a valid "file:///..." URI
    # from JOBS_DB_PATH on both Windows and Linux/Mac (plain string
    # concatenation like f"file:{JOBS_DB_PATH}" breaks on Windows paths,
    # which use backslashes and drive letters).
    db_uri = Path(JOBS_DB_PATH).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(db_uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_job(job_id: str, **fields) -> None:
    """
    Update fields on an existing job, e.g.:
        update_job(job_id, status=JobStatus.COMPLETE, report="...")

    Silently does nothing if the job_id doesn't exist or no fields were
    given - matches the original in-memory version's behavior.
    """
    if not fields:
        return

    # JobStatus is a Python Enum; SQLite needs its plain string value
    # (e.g. "complete"), not the Enum member itself.
    if "status" in fields and isinstance(fields["status"], JobStatus):
        fields["status"] = fields["status"].value

    # Builds e.g. "status = ?, report = ?" from whichever fields were
    # passed in, so callers can update just one field or several at once.
    set_clause = ", ".join(f"{column} = ?" for column in fields)
    values = list(fields.values()) + [job_id]

    with _get_connection() as conn:
        conn.execute(f"UPDATE jobs SET {set_clause} WHERE job_id = ?", values)
