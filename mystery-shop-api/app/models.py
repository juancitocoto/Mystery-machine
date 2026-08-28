"""
models.py
---------
Pydantic models define the "shape" of data going in and out of your API.
FastAPI uses these to validate requests automatically and to generate
your interactive docs at /docs.
"""

from pydantic import BaseModel
from typing import Optional
from enum import Enum


class JobStatus(str, Enum):
    """The possible states a mystery-shop report job can be in."""
    PENDING = "pending"
    TRANSCRIBING = "transcribing"
    GENERATING_REPORT = "generating_report"
    COMPLETE = "complete"
    FAILED = "failed"


class ShopJobResponse(BaseModel):
    """
    What we send back to the client right after they upload a file,
    and also what we send back when they check on job status later.

    The consent_* fields are an audit trail, not just request echoes:
    they persist what was attested about this recording's legal basis
    at submission time (see app/consent_law.py), in case a job's
    lawfulness is ever questioned later. They're Optional only to allow
    reading back rows from before these columns existed - every job
    created through the API now always has them set.
    """
    job_id: str
    status: JobStatus
    report: Optional[str] = None      # filled in once status == COMPLETE
    error_message: Optional[str] = None  # filled in if status == FAILED
    shop_state: Optional[str] = None
    consent_requirement: Optional[str] = None  # "one_party" / "all_party" - see ConsentRequirement in consent_law.py
    consent_attested: Optional[bool] = None
    employer_disclosure_attested: Optional[bool] = None
    created_at: Optional[str] = None  # UTC ISO 8601 timestamp; None only for legacy pre-migration rows


class SupportAgentRequest(BaseModel):
    """Body for POST /support/ask - just a plain-English question."""
    question: str


class SupportAgentResponse(BaseModel):
    """
    What POST /support/ask sends back.

    tool_calls_made is included for transparency, not because most
    callers need it: it's how many read-only database queries Claude ran
    to answer the question, so you can sanity-check "did it actually look
    anything up, or just guess?" while you're getting used to this
    feature.
    """
    answer: str
    tool_calls_made: int
