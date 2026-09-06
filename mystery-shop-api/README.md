# AlloMetrics

A FastAPI service, by Allophilism Connects, that takes an audio
recording of a mystery shop plus guidelines text, transcribes the
audio, and generates a 3-5 paragraph report using a Claude-powered
skill.

## How it works

1. `POST /shops` — upload an audio file + guidelines text, plus recording-consent details (`shop_state`, `recording_medium`, `recording_location_type`, `consent_attested`, and `employer_disclosure_attested` where required — see "Recording consent compliance" below). Rejects the request outright if those details don't establish a lawful basis to have made the recording; otherwise returns a `job_id` immediately.
2. In the background, the audio is transcribed, then passed (with the guidelines) to Claude to generate the report — Claude works through the guidelines requirement-by-requirement first, then writes the narrative (see "Agent features" below). The audio file is deleted once this finishes (success or failure) — see `RETAIN_AUDIO_FILES`.
3. `GET /shops/{job_id}` — poll this to check status and retrieve the finished report.
4. `POST /support/ask` — ask a plain-English question about your job history (e.g. "how many CA shops failed compliance this month?"); Claude answers it by querying the job database itself. See "Agent features" below.

See `PLAN.md` in the original conversation for the full architecture writeup, or just read the comments in each file — they're written to explain *why*, not just *what*.

## Project structure

```
app/
├── config.py         # settings: max file size, allowed types, API key, MOCK_MODE, rate limits
├── models.py         # request/response shapes (Pydantic)
├── storage.py        # secure file upload + saving logic
├── consent_law.py    # recording-consent state classification + the compliance gate - see below
├── jobs.py           # job tracking, persisted to SQLite (JOBS_DB_PATH)
├── worker.py         # background transcription + report generation (agentic - see "Agent features")
├── support_agent.py  # POST /support/ask - the job-history Q&A agent, see "Agent features"
└── main.py           # the FastAPI app + endpoints
tests/
├── test_main.py         # end-to-end tests using FastAPI's TestClient
└── test_support_agent.py # focused tests for the read-only SQL safety guard
conftest.py           # test setup (forces MOCK_MODE on, sets a test API key)
DEPLOYMENT.md          # step-by-step Railway deployment walkthrough
DEPLOYMENT_RENDER.md    # step-by-step Render deployment walkthrough
requirements.txt       # runtime dependencies only (what the Docker image installs)
requirements-dev.txt    # + pytest/httpx, for running tests locally
Dockerfile              # builds a container image of the API
docker-compose.yml       # convenience for running that image locally
LEGAL_DISCLAIMER.md      # draft ToS clause + caveats for the consent-law feature - NOT legal advice, needs attorney review
```

## Setup

```bash
python -m venv venv
source venv/bin/activate   # on Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in real values:

```bash
cp .env.example .env   # on Windows: copy .env.example .env
```

- `API_KEY` — a secret you make up yourself; this is what *your* API
  clients send back to you in the `X-API-Key` header.
- `ASSEMBLYAI_API_KEY` — get a free key at https://www.assemblyai.com/
  (Settings → API Keys after signing up; no credit card required).
- `ANTHROPIC_API_KEY` — your Anthropic key, for report generation.

Skip all of this for now if you just want to try the app - it works
with no keys at all while `MOCK_MODE=true` (the default). See "Testing
without spending a cent" below.

## Run it

```bash
uvicorn app.main:app --reload
```

Visit `http://127.0.0.1:8000/docs` for interactive API docs.

## Testing without spending a cent

By default (`MOCK_MODE=true` in `config.py`), `worker.py` skips the real
transcription + Claude calls and returns realistic canned data instead.
That means the full `POST /shops` → background job → `GET /shops/{id}`
flow works right now, with no API keys and no cost — good for local
testing and for the automated tests below.

Run the test suite (note the `-dev` requirements file, which adds
pytest/httpx on top of the runtime dependencies):

```bash
pip install -r requirements-dev.txt
pytest
```

`tests/test_main.py` covers: the health check, API key auth (missing
and wrong key rejected), rejecting unsupported file types, the recording-
consent gate, the full upload → poll → completed-report flow, a 404 for
an unknown job id, `POST /support/ask`'s auth/validation/mock-mode
behavior, and rate limiting on both agent endpoints (conftest.py sets a
low test-only limit so this is verified in a handful of requests, not
dozens). `tests/test_support_agent.py` separately verifies the
read-only SQL guard directly - including a real attempt to write
through the "read-only" connection, to confirm it's actually rejected
and not just assumed to be.

## Using real transcription (AssemblyAI)

`_transcribe_audio_real()` in `worker.py` is wired up to
[AssemblyAI](https://www.assemblyai.com/) already - free tier, no
credit card required. To turn it on:

1. Sign up at assemblyai.com and grab an API key (Settings → API Keys).
2. Add it to your `.env`: `ASSEMBLYAI_API_KEY=your-key-here`
3. Set `MOCK_TRANSCRIPTION=false` in `.env`.

That's it - `POST /shops` now sends the uploaded audio to AssemblyAI for
real and uses the actual transcript. A typical few-minute shop
recording transcribes in well under a minute. If `ASSEMBLYAI_API_KEY`
is missing, the job fails with a clear error message (check
`error_message` on the job) instead of hanging or crashing the server.

## Using real report generation (Claude)

`_generate_report_real()` in `worker.py` is wired up to the Anthropic
API already. To turn it on:

1. Get an API key at https://console.anthropic.com/ (this is the
   developer console - a different site/account than claude.ai). This
   is a paid API (no free tier), but writing one report costs well
   under a cent with the default model.
2. Add it to your `.env`: `ANTHROPIC_API_KEY=your-key-here`
3. Set `MOCK_REPORT_GENERATION=false` in `.env`.

`transcript` and `guidelines_text` get sent to Claude (`claude-sonnet-5`
by default - override with `ANTHROPIC_MODEL` in `.env`) as a two-step
agent rather than one prompt -> one response - see "Agent features"
below for how that works and why. If you want to change the report's
style or structure, `REPORT_SYSTEM_PROMPT` at the top of the "real
implementations" section in `worker.py` is the one place to edit.

If `ANTHROPIC_API_KEY` is missing, the job fails with a clear error
message instead of hanging or crashing the server - same pattern as the
AssemblyAI integration above.

Both `MOCK_TRANSCRIPTION` and `MOCK_REPORT_GENERATION` default to
whatever `MOCK_MODE` is, so you can turn either real service on
independently while the other stays mocked - you don't have to build
both at once. Setting the single `MOCK_MODE` switch to `false` turns
*both* on together, once you're ready for that.

## Agent features

Two features here use Claude "agentically" - giving it a tool and
letting it decide how to use it, rather than one fixed prompt -> one
fixed response. Both use Anthropic's **Tool Runner**
(`client.beta.messages.tool_runner`), which runs on your own server (no
separate hosted agent environment to create or pay for) and automates
the loop of "Claude calls a tool -> we run it -> Claude sees the result
-> Claude decides what to do next" until Claude gives a final answer
with no more tool calls. (Anthropic also offers "Managed Agents" - a
separate, heavier product for long-running autonomous work in a
hosted cloud sandbox. Neither feature here needs that: both finish in
seconds, not hours, so Tool Runner is the right-sized tool.)

**Report generation** (`_generate_report_real()` in `worker.py`): instead
of handing Claude the transcript and guidelines and asking for a report
in one shot, it now works in two phases. First, Claude calls a
`record_finding` tool once for each distinct requirement it identifies
in the guidelines, citing transcript evidence for each one - a
structured pass through the guidelines before it writes a word of
prose. Only once every requirement has a recorded finding does it write
the final narrative report, using those findings as its working notes.
This tends to catch requirements that a single-shot prompt would skim
over in a long or complex guidelines list. `AGENT_MAX_ITERATIONS`
(default 8) caps how many tool calls one report can make, so a
malformed or unusually long guidelines list can't run up an open-ended
bill on a single request.

**Support agent** (`POST /support/ask`, in `app/support_agent.py`): ask
a plain-English question about your job history - "how many CA shops
failed compliance this month?", "what's the most common error
message?" - and Claude answers it by writing and running its own SQL
query against the jobs database through a `query_jobs` tool. This is an
operator-facing endpoint (it can surface error messages and consent
attestations), so it's behind the same API key auth as everything else -
don't expose it to end users or mystery shoppers.

The query tool is read-only through two INDEPENDENT layers, not one:
the SQL text itself is checked (must be a single `SELECT`, nothing else,
no semicolon-stacked statements), *and* the database connection it runs
through is opened in SQLite's own read-only mode - so even if the text
check somehow missed something, SQLite itself refuses to let the
connection write. `tests/test_support_agent.py` verifies the read-only
connection really does reject a write with a direct test, not just an
assumption. `MOCK_SUPPORT_AGENT` (defaults to `MOCK_MODE`) skips the
real Claude call for testing, same pattern as the other mock flags;
`RATE_LIMIT_SUPPORT_AGENT` (default `10/minute`) and
`AGENT_MAX_ITERATIONS` apply the same reasoning as the report-generation
agent above.

## Recording consent compliance

**Read `app/consent_law.py`'s module docstring and `LEGAL_DISCLAIMER.md`
before relying on this section - this is a risk-reduction tool built by
Claude, which is not a lawyer, not a substitute for one.**

Recording someone without the right consent can be a criminal
wiretapping violation depending on the state, so `POST /shops` requires
every submission to declare how the recording was made and rejects
requests that don't establish a lawful basis for it - before the audio
file is even saved to disk. Five fields are involved:

- `shop_state` — two-letter US state/DC code, e.g. `"TX"`. This is a
  dropdown of all 51 real codes in `/docs` (the `USState` enum in
  `app/consent_law.py`, built from the same `US_STATE_CODES` set the
  compliance logic uses) rather than a free-text box - a made-up code
  gets rejected before the request even reaches the compliance checks.
- `recording_medium` — `"in_person"` or `"phone_call"`. A couple of
  states (Connecticut, Oregon) require different consent levels
  depending on which one this is.
- `recording_location_type` — `"public_area"` (sales floor, showroom,
  checkout, lobby) or `"private_area"` (break room, manager's office,
  fitting room, restroom). `private_area` is **rejected unconditionally**
  - no state or consent combination overrides this; a legitimate
    mystery shop has no reason to record there.
- `consent_attested` — confirms the shopper (a party to the
  conversation) consented to recording it. Required for every request.
- `employer_disclosure_attested` — only required in all-party-consent
  states: confirms the location has an active employee
  monitoring/recording disclosure (e.g. an employee-handbook clause)
  covering the person being recorded.

**The design fails closed.** `app/consent_law.py`'s state table only
classifies a state as `one_party` or `all_party` where multiple
independent research passes agreed without qualification. Every other
state defaults to `requires_review`, which blocks `POST /shops` for that
state with a 422 explaining why - not a guess, a hard stop, until a
human edits the table. As of this writing that's most states; filling in
the rest (attorney-verified) is on you before you can accept shops from
them. Michigan is a deliberate example: multiple sources call it
one-party, but with a "the statute actually says otherwise, courts carved
out an exception, and it's applied inconsistently" caveat attached, so
it's left as `requires_review` rather than guessed at.

Every attestation gets persisted on the job record (`shop_state`,
`consent_requirement`, `consent_attested`, `employer_disclosure_attested`
- visible in both the `POST /shops` response and `GET /shops/{job_id}`)
as an audit trail: if a job's lawfulness is ever questioned, there's a
durable record of what was confirmed at submission time.

Two things this does NOT do: verify that an attestation is actually
true (a client could claim `consent_attested: true` falsely - this
records what was attested, not what happened), or account for every
state-law nuance (only Connecticut and Oregon get medium-specific
handling right now; other hybrid rules may exist that this table doesn't
capture yet).

Separately, `RETAIN_AUDIO_FILES` (default `false`) deletes the uploaded
audio file once its job finishes, success or failure - see
`LEGAL_DISCLAIMER.md`'s "processing vault" section for what this claim
does and doesn't cover before repeating it to a client.

## Rate limiting

Each client (identified by IP address) is limited to a configurable
number of requests per minute:

- `POST /shops` — `RATE_LIMIT_CREATE_SHOP`, defaults to `5/minute`.
  Tighter, since each request triggers real transcription + Claude
  costs once `MOCK_MODE` is off.
- `GET /shops/{job_id}` — `RATE_LIMIT_GET_SHOP`, defaults to
  `60/minute`. Much looser, since it's just a cheap database read and
  clients are expected to poll it repeatedly while waiting on a job.
- `POST /support/ask` — `RATE_LIMIT_SUPPORT_AGENT`, defaults to
  `10/minute`. Tighter than a plain read, same reasoning as `POST /shops`
  - each real question costs real Claude API usage.

Going over a limit returns `429` in the same `{"detail": "..."}` shape
as this app's other errors - no special handling needed on the client
side. Override either limit in `.env` (format: `"<count>/<period>"`,
e.g. `"20/minute"` - see the
[`limits` library's rate-limit string notation](https://limits.readthedocs.io/en/stable/quickstart.html#rate-limit-string-notation)
for other periods like `/hour` or `/day`).

Two things worth knowing before you rely on this in production:

- Limits are stored in memory (`slowapi`'s default). Counts reset
  whenever the server restarts, and aren't shared across multiple
  server processes - fine for a single instance, but if you run several
  behind a load balancer, each one enforces its own separate counter
  unless you switch to Redis-backed storage (`storage_uri="redis://..."`
  when constructing `Limiter()` in `main.py`).
- Rate limiting identifies clients by IP address. Behind a reverse proxy
  (Railway, Render, Fly.io, or your own nginx), every request may
  arrive from the proxy's IP rather than the real client's - which
  would rate-limit *everyone* together as one client. Run uvicorn with
  `--proxy-headers` (and `--forwarded-allow-ips` set to your proxy) so
  it reads the real client IP from the `X-Forwarded-For` header
  instead. Most PaaS platforms handle this for you automatically, but
  it's worth verifying once you deploy.

## Running with Docker

You don't need Python installed on the machine you deploy to - Docker
packages the app and all its dependencies into one image that runs the
same way everywhere.

**Build the image:**

```bash
docker build -t mystery-shop-api .
```

**Run it**, passing in your `.env` file for secrets and mapping port 8000
so you can reach it from your browser:

```bash
docker run --env-file .env -p 8000:8000 mystery-shop-api
```

Visit `http://127.0.0.1:8000/docs` — same as running it without Docker.

**Or, for local development**, `docker compose up` does the same thing
(reading `docker-compose.yml`) plus keeps `uploaded_audio/` on your
machine across restarts:

```bash
docker compose up --build
```

A few things worth knowing:

- The image only installs `requirements.txt` (not `requirements-dev.txt`)
  and only copies in `app/` — no tests, no venv, no `.git` folder. See
  `.dockerignore` for the full exclude list.
- `MOCK_MODE` defaults to `true` even inside the container, so
  `docker run --env-file .env -p 8000:8000 mystery-shop-api` works out of
  the box with no API keys at all - handy for a quick smoke test.
- Deploying to Railway, Render, or Fly.io: each of those can build
  straight from this `Dockerfile` - point them at the repo and set your
  environment variables (`API_KEY`, `ANTHROPIC_API_KEY`, etc.) in their
  dashboard instead of shipping a `.env` file.
- Job history is stored in SQLite at `JOBS_DB_PATH` (`jobs.db` by
  default). A plain `docker run` keeps that file *inside* the
  container, so it's wiped out when the container is recreated -
  `docker compose up` avoids that by mounting `./data` as a volume and
  pointing `JOBS_DB_PATH` at a file inside it (see `docker-compose.yml`).
  If you deploy elsewhere, mount a persistent volume the same way, or
  the job history resets on every redeploy - **including the
  recording-consent audit trail**, so this isn't optional once you're
  handling real shops.
- `CMD` in the Dockerfile listens on `$PORT` if the hosting platform
  sets one (Railway, Render, Fly.io, etc. each pick their own port and
  expect your app to use it), falling back to `8000` for local
  `docker run`/`docker compose up` where nothing sets it - no changes
  needed either way.

**Deploying to Railway specifically:** see `DEPLOYMENT.md` for a full
walkthrough, including the persistent-volume step above in more detail.

**Deploying to Render specifically:** see `DEPLOYMENT_RENDER.md` -
this repo's root-level `render.yaml` builds straight from this monorepo,
no separate repo needed.

## Status

- ✅ Upload validation, secure file storage, background processing, API key auth
- ✅ SQLite-backed job store — job history survives a server restart
- ✅ Mock mode (`MOCK_MODE=true`) — exercise the whole pipeline for free
- ✅ Automated tests (pytest + FastAPI's TestClient)
- ✅ Dockerfile — one-command deploys to Railway, Render, Fly.io, etc.
- ✅ Real transcription via AssemblyAI (`_transcribe_audio_real()`) — set `ASSEMBLYAI_API_KEY` + `MOCK_TRANSCRIPTION=false`
- ✅ Real report generation via Claude (`_generate_report_real()`) — set `ANTHROPIC_API_KEY` + `MOCK_REPORT_GENERATION=false`
- ✅ Rate limiting per client IP — `RATE_LIMIT_CREATE_SHOP` / `RATE_LIMIT_GET_SHOP` / `RATE_LIMIT_SUPPORT_AGENT`
- ✅ Recording-consent compliance gate (`app/consent_law.py`) — fails closed by state/medium/location, audio auto-deleted after processing — see "Recording consent compliance" above and `LEGAL_DISCLAIMER.md`
- ✅ Agentic report generation — Claude records a finding per guideline requirement before writing the narrative, via Tool Runner — see "Agent features" above
- ✅ `POST /support/ask` — a job-history Q&A agent with a read-only, engine-level-enforced SQL tool — see "Agent features" above

## Next steps / ideas

- Move from SQLite to Postgres if you outgrow a single server / single file
- Move background processing to Celery + Redis if volume grows
- Switch rate limiting to Redis-backed storage if you run multiple server processes
- Tune `REPORT_SYSTEM_PROMPT` in `worker.py` once you've seen real
  reports come back — the current version is a solid generic starting
  point, not tailored to any one client's exact guidelines format
- Finish the state-by-state table in `app/consent_law.py` — most states
  are still `requires_review` on purpose (see "Recording consent
  compliance" above); each addition needs an attorney-verifiable source,
  not just a majority-of-blogs-agree guess
- If `POST /support/ask` gets slow or expensive on a big job history,
  consider giving it a second, more restrictive tool (e.g. one that only
  allows pre-approved query "shapes") instead of arbitrary SELECT - or
  move to Managed Agents if you outgrow Tool Runner's single-request
  model entirely (e.g. you want a persistent chat session instead of
  one question per call)
