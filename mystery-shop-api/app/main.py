"""
main.py
-------
This is the entry point of the API. Run it with:

    uvicorn app.main:app --reload

Then open http://127.0.0.1:8000/docs to see the interactive API docs
FastAPI generates automatically from your endpoints and models.
"""

import hmac

from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, BackgroundTasks, Header, Request

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import API_KEY, RATE_LIMIT_CREATE_SHOP, RATE_LIMIT_GET_SHOP, RATE_LIMIT_SUPPORT_AGENT
from app.models import ShopJobResponse, JobStatus, SupportAgentRequest, SupportAgentResponse
from app.storage import save_upload_securely
from app.jobs import create_job, get_job
from app.worker import process_shop_job
from app.consent_law import check_consent_basis, RecordingMedium, RecordingLocationType, USState
from app.support_agent import answer_question

app = FastAPI(
    title="AlloMetrics",
    description="Mystery shop report generation API, by Allophilism Connects.",
)


# ---------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------
# Limits each client to a configurable number of requests per time
# window (see RATE_LIMIT_* in config.py), identified by IP address via
# get_remote_address(). If a client goes over, slowapi raises an
# exception that's a subclass of FastAPI's own HTTPException - so it
# gets a 429 response shaped just like this app's other errors
# ({"detail": "..."}), with no extra setup needed.
#
# Uses in-memory storage by default: counts reset on restart and aren't
# shared across multiple server processes. Fine for a single-process
# deployment; if you run several instances behind a load balancer, pass
# storage_uri="redis://..." to Limiter() so they share one counter.
#
# Also note: get_remote_address() reads the direct TCP connection's IP.
# Behind a reverse proxy (common on Railway/Render/Fly.io, or your own
# nginx), that may be the proxy's IP for every request rather than each
# real client's - which would rate-limit everyone together as "one
# client." Run uvicorn with --proxy-headers (and --forwarded-allow-ips
# set to your proxy's IP) so it recovers the real client IP from the
# X-Forwarded-For header instead.
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter


# ---------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------
def require_api_key(x_api_key: str = Header(...)) -> None:
    """
    A FastAPI "dependency" - a function that runs before your endpoint
    code. Adding `Depends(require_api_key)` to any endpoint means it
    checks for a valid API key first, and rejects the request with a
    401 error if it's missing or wrong.

    Client usage: send a header like  X-API-Key: your-secret-key
    """
    # hmac.compare_digest avoids leaking timing info about how much of
    # the key matched, unlike a plain `!=` string comparison. It requires
    # both arguments to be the same type - `API_KEY or ""` keeps this safe
    # (always rejects) if API_KEY isn't set, instead of raising a TypeError
    # against None.
    if not hmac.compare_digest(x_api_key, API_KEY or ""):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ---------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------

@app.get("/health")
def health_check():
    """Simple endpoint to confirm the server is up. No auth needed."""
    return {"status": "ok"}


@app.post("/shops", response_model=ShopJobResponse, dependencies=[Depends(require_api_key)])
@limiter.limit(RATE_LIMIT_CREATE_SHOP)
async def create_shop_report(
    request: Request,
    background_tasks: BackgroundTasks,
    audio_file: UploadFile = File(...),
    guidelines_text: str = Form(...),
    shop_state: USState = Form(
        ...,
        description="Two-letter US state/DC postal code where the shop took place, e.g. 'TX'. Rendered as a dropdown in /docs.",
    ),
    recording_medium: RecordingMedium = Form(
        ...,
        description="Was this an in-person visit or a phone call? A few states' consent rules depend on it.",
    ),
    recording_location_type: RecordingLocationType = Form(
        ...,
        description=(
            "public_area (sales floor, showroom, checkout, lobby) or private_area "
            "(break room, manager's office, fitting room, restroom - always rejected)."
        ),
    ),
    consent_attested: bool = Form(
        ...,
        description="Confirms the shopper (a party to the recorded conversation) consented to recording it.",
    ),
    employer_disclosure_attested: bool = Form(
        False,
        description=(
            "Only required in all-party-consent states: confirms the location has an "
            "active employee recording/monitoring disclosure in place."
        ),
    ),
):
    """
    Upload an audio recording + guidelines text to start generating a
    mystery-shop report.

    Before anything else, this checks that the request establishes a
    lawful basis to have recorded the conversation under the declared
    state's consent law (see app/consent_law.py) - if it doesn't, the
    request is rejected with a 422 explaining why, and the audio file is
    never even saved to disk. This is a compliance AID, not a legal
    guarantee - see the big warning at the top of consent_law.py.

    Once that check passes, this returns immediately with a job_id and
    status "pending" - the actual transcription + report generation
    happens in the background because it can take a while. Poll
    GET /shops/{job_id} to check progress.

    Rate-limited per client IP (RATE_LIMIT_CREATE_SHOP in config.py) -
    exceeding it returns 429. The `request: Request` parameter isn't
    used by this function's own logic; it's required by the
    @limiter.limit(...) decorator above, which inspects it to identify
    the calling client.
    """
    # 1. Legal gate FIRST - reject before the audio file ever touches
    #    disk, and before a job record is even created.
    consent_requirement = check_consent_basis(
        shop_state, recording_medium, recording_location_type, consent_attested, employer_disclosure_attested
    )

    # 2. Validate + securely save the uploaded audio file.
    audio_path = await save_upload_securely(audio_file)

    # 3. Create a job record so we can track progress - persisting the
    #    consent attestations too, as an audit trail for this job.
    job = create_job(
        shop_state=shop_state.strip().upper(),
        consent_requirement=consent_requirement.value,
        consent_attested=consent_attested,
        employer_disclosure_attested=employer_disclosure_attested,
        recording_medium=recording_medium.value,
        recording_location_type=recording_location_type.value,
    )

    # 4. Kick off the slow work in the background. FastAPI runs this
    #    AFTER the response below has already been sent to the client.
    background_tasks.add_task(process_shop_job, job.job_id, audio_path, guidelines_text)

    # 5. Respond right away - don't make the client wait.
    return job


@app.get("/shops/{job_id}", response_model=ShopJobResponse, dependencies=[Depends(require_api_key)])
@limiter.limit(RATE_LIMIT_GET_SHOP)
def get_shop_report(request: Request, job_id: str):
    """
    Check the status of a job, and retrieve the finished report once
    status is "complete".

    Rate-limited per client IP (RATE_LIMIT_GET_SHOP in config.py) - a
    much higher limit than POST /shops since this is just a cheap
    database read, and clients are expected to poll it repeatedly.
    """
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/support/ask", response_model=SupportAgentResponse, dependencies=[Depends(require_api_key)])
@limiter.limit(RATE_LIMIT_SUPPORT_AGENT)
async def ask_support_agent(request: Request, body: SupportAgentRequest):
    """
    Ask a plain-English question about your job history, e.g. "how many
    CA shops failed compliance this month?" - see app/support_agent.py
    for how this works (Claude gets a read-only SQL tool against the
    jobs database and answers using it).

    This is an internal/operator-facing endpoint (it can surface job
    details like error messages and consent attestations), so it's
    behind the same API key auth as the rest of this API - not something
    to expose to end users or mystery shoppers.

    Rate-limited (RATE_LIMIT_SUPPORT_AGENT in config.py) since a real
    answer costs real Claude API usage, same reasoning as POST /shops.
    """
    try:
        return await answer_question(body.question)
    except RuntimeError as e:
        # Missing ANTHROPIC_API_KEY, most likely - a clear 500 rather
        # than an unhandled crash. (Real query/SQL problems are handled
        # inside support_agent.py itself and fed back to Claude, not
        # raised here - this only catches setup problems.)
        raise HTTPException(status_code=500, detail=str(e))
