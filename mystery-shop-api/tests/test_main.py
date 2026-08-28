"""
tests/test_main.py
-------------------
End-to-end tests for the API, using FastAPI's TestClient (built on
httpx). These don't start a real server or make real network calls -
TestClient runs the app in-process, which is fast and doesn't need
uvicorn running.

Because MOCK_MODE is forced on in conftest.py, these tests never touch
a real transcription service or the Anthropic API - they run for free,
every time, including in CI.

Run with:
    pytest
(from the mystery-shop-api project root, i.e. the folder this file's
tests/ directory lives in)
"""

import io
import os
import time

from fastapi.testclient import TestClient

from app.main import app
from app.config import API_KEY, RATE_LIMIT_CREATE_SHOP, UPLOAD_DIR

client = TestClient(app)

AUTH_HEADERS = {"X-API-Key": API_KEY}


def _fake_audio_file():
    """
    Builds a small in-memory fake "audio" file for upload tests.

    The API only checks the file *extension* and size - it doesn't
    validate that the bytes are really a valid audio file - so plain
    bytes with a ".wav" name are enough to exercise the upload path.
    """
    return ("shop_visit.wav", io.BytesIO(b"fake audio bytes for testing"), "audio/wav")


def _valid_shop_form(**overrides):
    """
    A complete, compliant set of POST /shops form fields, as strings
    (matching how a real HTTP client sends form data - Python bools
    would get stringified as "True"/"False", which FastAPI's bool
    parsing doesn't accept the same way as "true"/"false").

    Defaults to Texas - a one-party-consent state per app/consent_law.py
    - with every attestation set to a compliant value, so any test that
    isn't specifically exercising the consent-law gate can just call
    this with no arguments and get a request that sails through it.
    Pass overrides to test a specific rejection path, e.g.
    _valid_shop_form(shop_state="CA") to test an all-party state.
    """
    base = {
        "guidelines_text": "Greet within 30 seconds.",
        "shop_state": "TX",
        "recording_medium": "in_person",
        "recording_location_type": "public_area",
        "consent_attested": "true",
        "employer_disclosure_attested": "false",
    }
    base.update(overrides)
    return base


def _uploaded_audio_file_count():
    """How many files currently sit in UPLOAD_DIR - used to confirm a rejected/completed request didn't leave audio behind."""
    if not os.path.isdir(UPLOAD_DIR):
        return 0
    return len(os.listdir(UPLOAD_DIR))


# ---------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------

def test_health_check_does_not_require_auth():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------

def test_create_shop_report_requires_api_key():
    """No X-API-Key header at all -> FastAPI's Header(...) rejects with 422/401."""
    response = client.post(
        "/shops",
        files={"audio_file": _fake_audio_file()},
        data=_valid_shop_form(),
    )
    # Missing a required header is a client error either way; the
    # important thing is it's rejected, not that a job gets created.
    assert response.status_code in (401, 422)


def test_create_shop_report_rejects_wrong_api_key():
    response = client.post(
        "/shops",
        headers={"X-API-Key": "definitely-not-the-right-key"},
        files={"audio_file": _fake_audio_file()},
        data=_valid_shop_form(),
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------

def test_create_shop_report_rejects_unsupported_file_type():
    bad_file = ("notes.txt", io.BytesIO(b"just some text"), "text/plain")
    response = client.post(
        "/shops",
        headers=AUTH_HEADERS,
        files={"audio_file": bad_file},
        data=_valid_shop_form(),
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------
# Recording-consent gate (see app/consent_law.py)
#
# check_consent_basis() runs BEFORE the audio file is saved, so every
# rejection test below also confirms no file was left behind in
# UPLOAD_DIR - a request that fails this gate should leave no trace.
# ---------------------------------------------------------------------

def test_create_shop_report_rejects_private_area_regardless_of_state_or_consent():
    """Private-area recordings are blocked unconditionally - even a compliant one-party state + full consent doesn't override this."""
    before = _uploaded_audio_file_count()
    response = client.post(
        "/shops",
        headers=AUTH_HEADERS,
        files={"audio_file": _fake_audio_file()},
        data=_valid_shop_form(recording_location_type="private_area"),
    )
    assert response.status_code == 422
    assert "private area" in response.json()["detail"].lower()
    assert _uploaded_audio_file_count() == before


def test_create_shop_report_rejects_unrecognized_state_code():
    """
    shop_state is a dropdown (a USState enum) in /docs, not a free-text
    box - see app/consent_law.py's USState - so a made-up code like "ZZ"
    is now rejected by FastAPI's own request validation before this
    request ever reaches check_consent_basis(). That means a DIFFERENT
    response shape than the app's other custom errors: FastAPI's
    validation "detail" is a list of error objects, not a plain string -
    still a 422, and audio still never gets saved either way.
    """
    before = _uploaded_audio_file_count()
    response = client.post(
        "/shops",
        headers=AUTH_HEADERS,
        files={"audio_file": _fake_audio_file()},
        data=_valid_shop_form(shop_state="ZZ"),
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)  # FastAPI's own enum-validation error shape
    assert any("shop_state" in error.get("loc", []) for error in detail)
    assert _uploaded_audio_file_count() == before


def test_create_shop_report_rejects_unclassified_state():
    """A real US state that consent_law.py hasn't classified (REQUIRES_REVIEW) blocks the request rather than guessing."""
    before = _uploaded_audio_file_count()
    response = client.post(
        "/shops",
        headers=AUTH_HEADERS,
        files={"audio_file": _fake_audio_file()},
        data=_valid_shop_form(shop_state="MI"),  # deliberately left unclassified - see consent_law.py
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "hasn't confirmed" in detail.lower()
    # shop_state is a USState enum under the hood (see consent_law.py) -
    # this message must read "MI", not the enum's raw repr "USState.MI".
    assert "usstate" not in detail.lower()
    assert "MI" in detail
    assert _uploaded_audio_file_count() == before


def test_create_shop_report_one_party_state_requires_consent_attested():
    before = _uploaded_audio_file_count()
    response = client.post(
        "/shops",
        headers=AUTH_HEADERS,
        files={"audio_file": _fake_audio_file()},
        data=_valid_shop_form(consent_attested="false"),
    )
    assert response.status_code == 422
    assert "consent_attested" in response.json()["detail"]
    assert _uploaded_audio_file_count() == before


def test_create_shop_report_all_party_state_requires_employer_disclosure():
    """California is all-party consent - shopper consent alone isn't enough without employer_disclosure_attested too."""
    before = _uploaded_audio_file_count()
    response = client.post(
        "/shops",
        headers=AUTH_HEADERS,
        files={"audio_file": _fake_audio_file()},
        data=_valid_shop_form(shop_state="CA", consent_attested="true", employer_disclosure_attested="false"),
    )
    assert response.status_code == 422
    assert "all-party consent" in response.json()["detail"].lower()
    assert _uploaded_audio_file_count() == before


def test_create_shop_report_all_party_state_succeeds_with_both_attestations():
    response = client.post(
        "/shops",
        headers=AUTH_HEADERS,
        files={"audio_file": _fake_audio_file()},
        data=_valid_shop_form(shop_state="CA", consent_attested="true", employer_disclosure_attested="true"),
    )
    assert response.status_code == 200
    assert response.json()["status"] in ("pending", "transcribing", "generating_report", "complete")


def test_create_shop_report_connecticut_medium_specific_override():
    """
    Connecticut is a documented case of a state whose requirement
    depends on recording medium, not just location - all-party for phone
    calls, one-party for in-person. Same state, same consent attested,
    different medium -> different outcome.
    """
    # Phone call: all-party required, no employer disclosure attested -> rejected.
    phone_response = client.post(
        "/shops",
        headers=AUTH_HEADERS,
        files={"audio_file": _fake_audio_file()},
        data=_valid_shop_form(
            shop_state="CT",
            recording_medium="phone_call",
            consent_attested="true",
            employer_disclosure_attested="false",
        ),
    )
    assert phone_response.status_code == 422
    assert "all-party consent" in phone_response.json()["detail"].lower()

    # In-person: one-party is enough - same shopper consent, no employer disclosure needed.
    in_person_response = client.post(
        "/shops",
        headers=AUTH_HEADERS,
        files={"audio_file": _fake_audio_file()},
        data=_valid_shop_form(
            shop_state="CT",
            recording_medium="in_person",
            consent_attested="true",
            employer_disclosure_attested="false",
        ),
    )
    assert in_person_response.status_code == 200


# ---------------------------------------------------------------------
# Full happy-path flow: upload -> job_id -> poll -> completed report
# ---------------------------------------------------------------------

def test_full_upload_and_report_flow():
    # 1. Upload audio + guidelines, expect a job_id back right away.
    create_response = client.post(
        "/shops",
        headers=AUTH_HEADERS,
        files={"audio_file": _fake_audio_file()},
        data=_valid_shop_form(
            guidelines_text="Associates should greet shoppers within 30 seconds and not be pushy."
        ),
    )
    assert create_response.status_code == 200

    body = create_response.json()
    assert "job_id" in body and body["job_id"]
    assert body["status"] in ("pending", "transcribing", "generating_report", "complete")
    # The consent audit trail should be on the job record too.
    assert body["shop_state"] == "TX"
    assert body["consent_requirement"] == "one_party"
    assert body["consent_attested"] is True

    job_id = body["job_id"]

    # 2. Poll GET /shops/{job_id} until the background job finishes.
    #    (In practice this often completes before we even get here, since
    #    TestClient waits for background tasks - but polling makes the
    #    test robust either way, just like a real API client would do.)
    final_status = None
    final_body = None
    for _ in range(50):
        poll_response = client.get(f"/shops/{job_id}", headers=AUTH_HEADERS)
        assert poll_response.status_code == 200
        final_body = poll_response.json()
        final_status = final_body["status"]
        if final_status in ("complete", "failed"):
            break
        time.sleep(0.05)

    assert final_status == "complete", f"job did not complete, last body: {final_body}"
    assert final_body["report"] is not None
    assert "[MOCK REPORT" in final_body["report"]
    assert final_body["error_message"] is None


def test_audio_file_deleted_after_job_completes():
    """
    RETAIN_AUDIO_FILES defaults to False (see config.py / conftest.py),
    so the uploaded file should be gone from disk once the job finishes
    - not just processed, actually deleted. This is what makes the
    "we don't store your audio long-term" claim true rather than aspirational.
    """
    before = _uploaded_audio_file_count()

    create_response = client.post(
        "/shops",
        headers=AUTH_HEADERS,
        files={"audio_file": _fake_audio_file()},
        data=_valid_shop_form(),
    )
    assert create_response.status_code == 200
    job_id = create_response.json()["job_id"]

    for _ in range(50):
        poll_response = client.get(f"/shops/{job_id}", headers=AUTH_HEADERS)
        if poll_response.json()["status"] in ("complete", "failed"):
            break
        time.sleep(0.05)

    # Back to however many files existed before this test ran - the one
    # this test uploaded should have been cleaned up, not left behind.
    assert _uploaded_audio_file_count() == before


# ---------------------------------------------------------------------
# 404 for unknown jobs
# ---------------------------------------------------------------------

def test_shop_state_is_a_dropdown_of_all_51_codes_in_openapi_schema():
    """
    shop_state should render as a dropdown in /docs, not a free-text box
    - confirmed here by checking the generated OpenAPI schema actually
    lists all 50 states + DC as an enum, rather than trusting that
    "it's an Enum type" alone guarantees Swagger UI renders it that way.
    """
    schema = client.get("/openapi.json").json()
    us_state_schema = schema["components"]["schemas"].get("USState")
    assert us_state_schema is not None
    assert us_state_schema.get("enum") is not None
    assert len(us_state_schema["enum"]) == 51  # 50 states + DC
    assert "TX" in us_state_schema["enum"]
    assert "ZZ" not in us_state_schema["enum"]


def test_get_shop_report_404_for_unknown_job_id():
    response = client.get("/shops/this-job-id-does-not-exist", headers=AUTH_HEADERS)
    assert response.status_code == 404


# ---------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------

def test_support_agent_requires_api_key():
    response = client.post("/support/ask", json={"question": "how many jobs are there?"})
    assert response.status_code in (401, 422)


def test_support_agent_rejects_wrong_api_key():
    response = client.post(
        "/support/ask",
        headers={"X-API-Key": "definitely-not-the-right-key"},
        json={"question": "how many jobs are there?"},
    )
    assert response.status_code == 401


def test_support_agent_answers_in_mock_mode():
    """
    MOCK_SUPPORT_AGENT defaults to MOCK_MODE (forced on in conftest.py),
    so this never calls Claude - but it DOES exercise the real endpoint,
    auth, and a real (harmless) query against the real test database, so
    it still catches a broken response shape or a broken DB connection.
    """
    response = client.post(
        "/support/ask",
        headers=AUTH_HEADERS,
        json={"question": "how many jobs are there?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "[MOCK SUPPORT AGENT ANSWER" in body["answer"]
    assert body["tool_calls_made"] == 0


def test_support_agent_rejects_missing_question_field():
    response = client.post("/support/ask", headers=AUTH_HEADERS, json={})
    assert response.status_code == 422


def test_support_agent_rate_limit_enforced():
    """Same reasoning/pattern as test_create_shop_report_rate_limit_enforced below - RATE_LIMIT_SUPPORT_AGENT is set low in conftest.py specifically so this can verify it in a handful of requests."""
    max_requests = int(os.environ["RATE_LIMIT_SUPPORT_AGENT"].split("/")[0])

    responses = [
        client.post(
            "/support/ask",
            headers=AUTH_HEADERS,
            json={"question": "how many jobs are there?"},
        )
        for _ in range(max_requests + 1)
    ]

    for response in responses[:max_requests]:
        assert response.status_code == 200

    over_limit_response = responses[-1]
    assert over_limit_response.status_code == 429
    assert "detail" in over_limit_response.json()


def test_create_shop_report_rate_limit_enforced():
    """
    conftest.py sets RATE_LIMIT_CREATE_SHOP to a low test-only value
    (e.g. "3/minute") specifically so this test can verify the 429
    behavior in a handful of requests. We read the actual configured
    number back out of RATE_LIMIT_CREATE_SHOP rather than hardcoding it,
    so this test keeps working even if that test value changes later.
    """
    max_requests = int(RATE_LIMIT_CREATE_SHOP.split("/")[0])

    responses = [
        client.post(
            "/shops",
            headers=AUTH_HEADERS,
            files={"audio_file": _fake_audio_file()},
            data=_valid_shop_form(),
        )
        for _ in range(max_requests + 1)
    ]

    # Every request up to the limit should succeed normally...
    for response in responses[:max_requests]:
        assert response.status_code == 200

    # ...and the one request that goes over it should be rejected, in
    # the same {"detail": "..."} shape as this app's other errors.
    over_limit_response = responses[-1]
    assert over_limit_response.status_code == 429
    assert "detail" in over_limit_response.json()
