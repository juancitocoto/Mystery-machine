"""
support_agent.py
-----------------
A small internal "agent" you can ask plain-English questions about your
job history, e.g. "how many CA shops failed compliance this month?" or
"what's the most common error message?" - see POST /support/ask in
main.py.

Uses Anthropic's Tool Runner (client.beta.messages.tool_runner) instead
of a single prompt -> response call: Claude is given ONE tool - a
read-only SQL query against the jobs database - and the runner
automatically loops "Claude writes a query -> we run it -> Claude sees
the result -> Claude decides whether it needs another query or is ready
to answer" until it gives a final plain-text answer with no more tool
calls. This all runs on your own server; there's no separate hosted
agent environment or extra infrastructure to set up or pay for.

Safety: the query tool enforces two INDEPENDENT layers, so a bad or
adversarial query can't do damage or leak beyond what's asked:
  1. The SQL text must be a single SELECT statement (checked in this
     file, in _reject_reason()).
  2. The database connection itself is opened read-only at the SQLite
     engine level (see run_read_only_query() in jobs.py) - even if layer
     1 somehow missed something, SQLite itself refuses the write; this
     was verified directly (a real INSERT/UPDATE attempt through that
     connection raises sqlite3.OperationalError) before this shipped.
Relying on just the text check alone (layer 1) is the kind of thing
that's easy to get subtly wrong - two independent layers is a better fit
for a system that holds shop data and legal-consent records.
"""

import asyncio
import json
import re
import sqlite3
import textwrap
from typing import Optional

from anthropic import AsyncAnthropic, beta_async_tool

from app.jobs import run_read_only_query
from app.models import SupportAgentResponse
from app.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, AGENT_MAX_ITERATIONS, MOCK_SUPPORT_AGENT


# The support agent only ever needs to read this one table - describing
# its exact columns (rather than letting Claude guess at names) means it
# writes correct SQL on the first try far more often.
JOBS_TABLE_SCHEMA = textwrap.dedent(
    """\
    Table: jobs
      job_id                        TEXT    unique job identifier
      status                        TEXT    one of: pending, transcribing, generating_report, complete, failed
      report                        TEXT    the finished narrative report (NULL until status = complete)
      error_message                 TEXT    set only if status = failed
      shop_state                    TEXT    two-letter US state/DC code, e.g. 'TX'
      consent_requirement           TEXT    'one_party' or 'all_party' - the consent law that applied to this shop
      consent_attested              INTEGER 0/1 - whether the shopper's consent was attested
      employer_disclosure_attested  INTEGER 0/1 - whether an employer recording disclosure was attested
      recording_medium              TEXT    'in_person' or 'phone_call'
      recording_location_type       TEXT    'public_area' or 'private_area'
      transcript                    TEXT    the raw transcript (NULL until transcription finishes)
      created_at                    TEXT    UTC ISO 8601 timestamp, e.g. '2026-08-27T14:03:11+00:00'
      updated_at                    TEXT    UTC ISO 8601 timestamp of the job's last status change
    """
)

SUPPORT_AGENT_SYSTEM_PROMPT = textwrap.dedent(
    f"""\
    You are an internal analyst for a mystery-shop reporting company.
    You answer plain-English questions about the company's job history by
    querying its SQLite database with the query_jobs tool.

    {JOBS_TABLE_SCHEMA}
    Rules:
    - query_jobs only accepts a single SELECT statement - no INSERT,
      UPDATE, DELETE, or multiple statements separated by semicolons.
    - created_at is ISO 8601 text, so plain string comparison works for
      date filtering, e.g. WHERE created_at >= '2026-08-01'.
    - Add a LIMIT (e.g. LIMIT 20) to exploratory queries unless you're
      computing an aggregate like COUNT(*) - you rarely need every
      matching row to answer a question, and large result sets waste
      tokens.
    - If a query fails, read the error message, fix the query, and try
      again - don't give up after one failed attempt without at least
      trying to correct an obvious mistake (e.g. a typo'd column name).
    - Once you have enough information, answer in 1-3 plain sentences.
      Don't dump raw query results - summarize what they mean. If the
      data doesn't support a confident answer (e.g. zero matching rows
      for the time range asked about), say so plainly instead of
      guessing or padding out a vague answer.
    """
)


def _reject_reason(sql: str) -> Optional[str]:
    """
    Returns a human-readable rejection reason if `sql` isn't a safe,
    single, read-only SELECT statement - or None if it looks safe to run.

    This is the FIRST, cheaper safety layer - it exists mainly to give
    Claude a clear reason to fix its own query. The actual hard backstop
    is the read-only database connection in jobs.py's
    run_read_only_query(), which physically can't write no matter what
    slips past this check.
    """
    stripped = sql.strip()
    if not stripped:
        return "Query is empty."

    # A single trailing semicolon is fine ("SELECT 1;" is one statement).
    # Anything else before/after a semicolon means multiple statements
    # are stacked together, which this tool doesn't allow.
    body = stripped[:-1] if stripped.endswith(";") else stripped
    if ";" in body:
        return "Only a single SQL statement is allowed - no semicolon-separated multiple statements."

    if not re.match(r"(?is)^\s*select\b", body):
        return "Only SELECT statements are allowed."

    # Belt-and-suspenders keyword check, in case one of these ever shows
    # up somewhere unexpected (e.g. inside a subquery) despite the
    # statement starting with SELECT.
    forbidden_keywords = ("insert", "update", "delete", "drop", "alter", "attach", "pragma", "vacuum", "replace")
    lowered = body.lower()
    for keyword in forbidden_keywords:
        if re.search(rf"\b{keyword}\b", lowered):
            return f"'{keyword.upper()}' is not allowed - this tool is read-only."

    return None


async def answer_question(question: str) -> SupportAgentResponse:
    """
    Answers one question about job history.

    Picks mock vs. real based on MOCK_SUPPORT_AGENT (see config.py) -
    same pattern as worker.py's transcribe_audio()/generate_report().
    """
    if MOCK_SUPPORT_AGENT:
        return await _mock_answer_question(question)
    return await _answer_question_real(question)


async def _mock_answer_question(question: str) -> SupportAgentResponse:
    """
    Fake agent answer - no Claude call, no cost. It doesn't exercise
    Claude's reasoning, but it DOES run a real (harmless) read-only query
    through the real database wiring, so this still catches a broken
    schema or a broken run_read_only_query() without needing an API key.
    """
    await asyncio.sleep(0.1)

    try:
        rows = run_read_only_query("SELECT COUNT(*) AS total FROM jobs")
        total = rows[0]["total"] if rows else 0
    except sqlite3.Error:
        total = "unknown"

    return SupportAgentResponse(
        answer=(
            "[MOCK SUPPORT AGENT ANSWER - generated because MOCK_SUPPORT_AGENT is on] "
            f"There are currently {total} job(s) in the database. "
            f"(Question received: {question!r})"
        ),
        tool_calls_made=0,
    )


async def _answer_question_real(question: str) -> SupportAgentResponse:
    """
    Runs the real agent: gives Claude the query_jobs tool and lets Tool
    Runner loop until it produces a final plain-text answer.
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file, "
            "or set MOCK_SUPPORT_AGENT=true to skip the real agent."
        )

    # A plain int can't be reassigned from inside a nested function
    # without "nonlocal" - a one-item list would also work, but nonlocal
    # reads more clearly here.
    call_count = 0

    @beta_async_tool
    async def query_jobs(sql: str) -> str:
        """Run a single read-only SELECT query against the jobs table and return the matching rows as JSON.

        Args:
            sql: A single SELECT statement. No INSERT/UPDATE/DELETE and no multiple statements separated by semicolons.
        """
        nonlocal call_count
        call_count += 1

        reason = _reject_reason(sql)
        if reason is not None:
            return json.dumps({"error": reason})

        try:
            rows = run_read_only_query(sql)
        except sqlite3.Error as e:
            return json.dumps({"error": f"Query failed: {e}"})

        return json.dumps({"row_count": len(rows), "rows": rows})

    client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    runner = client.beta.messages.tool_runner(
        model=ANTHROPIC_MODEL,
        max_tokens=1024,
        max_iterations=AGENT_MAX_ITERATIONS,
        system=SUPPORT_AGENT_SYSTEM_PROMPT,
        tools=[query_jobs],
        messages=[{"role": "user", "content": question}],
    )
    final_message = await runner.until_done()

    # final_message.content is a list of content blocks. Once Claude
    # stops calling tools, its answer is the text block(s) - joining all
    # of them (rather than assuming there's exactly one) is a bit more
    # robust than indexing content[0] directly.
    answer_text = "".join(
        block.text for block in final_message.content if getattr(block, "type", None) == "text"
    ).strip()

    if not answer_text:
        # Can happen if the agent hit AGENT_MAX_ITERATIONS while still
        # mid-tool-call, or otherwise never produced text - better to say
        # so than to return an empty string.
        answer_text = (
            "The agent didn't produce a final answer - it may have hit the "
            "tool-call limit before concluding. Try a more specific question."
        )

    return SupportAgentResponse(answer=answer_text, tool_calls_made=call_count)
