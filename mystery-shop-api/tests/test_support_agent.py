"""
tests/test_support_agent.py
----------------------------
Focused tests for the parts of app/support_agent.py that matter most:
the read-only SQL guard (_reject_reason) and the read-only database
connection (run_read_only_query, in app/jobs.py) it relies on.

These are tested directly, as plain Python, rather than only through
POST /support/ask - the guard is a security boundary (it's what stops a
malformed or adversarial query from writing to the database), so it
gets its own tests instead of relying solely on end-to-end coverage.
"""

import sqlite3

import pytest

from app.support_agent import _reject_reason
from app.jobs import run_read_only_query
from app.config import JOBS_DB_PATH


# ---------------------------------------------------------------------
# _reject_reason - the SQL text guard
# ---------------------------------------------------------------------

def test_reject_reason_allows_plain_select():
    assert _reject_reason("SELECT * FROM jobs") is None


def test_reject_reason_allows_select_with_trailing_semicolon():
    assert _reject_reason("SELECT COUNT(*) FROM jobs;") is None


def test_reject_reason_is_case_insensitive():
    assert _reject_reason("select * from jobs") is None


def test_reject_reason_blocks_empty_query():
    assert _reject_reason("") is not None
    assert _reject_reason("   ") is not None


def test_reject_reason_blocks_non_select_statements():
    for bad_sql in [
        "INSERT INTO jobs (job_id) VALUES ('x')",
        "UPDATE jobs SET status='failed'",
        "DELETE FROM jobs",
        "DROP TABLE jobs",
        "ALTER TABLE jobs ADD COLUMN x TEXT",
        "PRAGMA table_info(jobs)",
        "VACUUM",
    ]:
        reason = _reject_reason(bad_sql)
        assert reason is not None, f"expected {bad_sql!r} to be rejected"


def test_reject_reason_blocks_stacked_statements():
    """A SELECT followed by a second statement - a classic SQL injection shape - must be rejected even though it starts with SELECT."""
    reason = _reject_reason("SELECT * FROM jobs; DROP TABLE jobs;")
    assert reason is not None
    assert "single sql statement" in reason.lower()


def test_reject_reason_blocks_write_keyword_hidden_in_subquery():
    """Belt-and-suspenders check: a write keyword anywhere in the query text is blocked, not just when the statement doesn't start with SELECT."""
    reason = _reject_reason("SELECT * FROM (UPDATE jobs SET status='x')")
    assert reason is not None


# ---------------------------------------------------------------------
# run_read_only_query - the database-engine-level backstop
#
# This is the layer that matters even if _reject_reason above had a bug:
# the connection itself is opened read-only, so SQLite refuses writes
# outright. Confirmed here directly against the real (test) jobs.db.
# ---------------------------------------------------------------------

def test_run_read_only_query_can_select():
    rows = run_read_only_query("SELECT COUNT(*) AS total FROM jobs")
    assert len(rows) == 1
    assert "total" in rows[0]


def test_run_read_only_query_physically_cannot_write():
    """Even calling this function with a write statement directly (bypassing _reject_reason entirely) must fail, because the connection itself is read-only at the SQLite engine level."""
    with pytest.raises(sqlite3.Error):
        run_read_only_query("INSERT INTO jobs (job_id, status) VALUES ('should-not-work', 'pending')")

    # And to be extra sure: confirm that row never actually landed.
    rows = run_read_only_query("SELECT * FROM jobs WHERE job_id = 'should-not-work'")
    assert rows == []
