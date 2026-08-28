"""
config.py
---------
Central place for settings. Keeping these in one file means you can
change limits later without hunting through your whole codebase.
"""

import os
from dotenv import load_dotenv

# Reads the .env file (if present) and loads its values into the
# environment, so os.environ.get(...) below can see them. .env is in
# .gitignore, so your real secrets never get committed to git.
load_dotenv()

# Max upload size in bytes. 50 MB is generous for a phone-recorded shop.
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

# Only allow these file extensions. Whitelisting (not blacklisting) is safer.
ALLOWED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg"}

# Where uploaded audio files get saved on disk.
# NOTE: this folder should NOT be inside any directory your web server
# serves publicly (e.g. don't put this under "static/").
UPLOAD_DIR = "uploaded_audio"

# The shared-secret API key your clients (mystery shop companies) send
# back to you in the X-API-Key header. Set this in .env - do not hardcode
# a real value here, since this file is committed to git.
API_KEY = os.environ.get("API_KEY")

# Keys for the external services worker.py will call: AssemblyAI
# (transcription) and Anthropic (Claude, for report generation). Also
# set in .env. Get a free AssemblyAI key at https://www.assemblyai.com/
# (Settings -> API Keys after signing up - no credit card required).
ASSEMBLYAI_API_KEY = os.environ.get("ASSEMBLYAI_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# Which Claude model writes the report. Sonnet is a good default: strong
# writing quality for a fraction of Opus-tier cost. Override in .env if
# you want to try a different model.
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-5"

# Where job records (status, transcript, report, errors) are persisted.
# SQLite stores this as a single file - no separate database server to
# run. Using a real file (not memory) means job history survives a
# server restart. Override in .env if you want it somewhere else, e.g.
# a mounted volume path when running in Docker.
# `or "jobs.db"` (not just a .get default) so an accidentally-blank
# JOBS_DB_PATH= line in .env falls back too, instead of trying to open a
# database at an empty path.
JOBS_DB_PATH = os.environ.get("JOBS_DB_PATH") or "jobs.db"

def _env_flag(name: str, default: bool) -> bool:
    """Reads an env var as True/False (accepts "true"/"1"/"yes", case-insensitive)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in ("true", "1", "yes")


# Master switch: when True, worker.py skips ALL real external calls
# (transcription AND report generation) and returns realistic fake data
# instead. This lets you test the whole upload -> job -> report flow for
# free, before you've wired in (or paid for) any real services.
#
# Defaults to True so a fresh clone "just works" without any API keys.
MOCK_MODE = _env_flag("MOCK_MODE", default=True)

# Per-step overrides, for when you've wired up ONE real service but not
# the other yet (e.g. real transcription is ready, but you haven't
# plugged in your Claude skill for report generation). Each defaults to
# whatever MOCK_MODE is - you only need to set one of these in .env if
# you want that particular step to behave differently than MOCK_MODE
# alone would suggest.
MOCK_TRANSCRIPTION = _env_flag("MOCK_TRANSCRIPTION", default=MOCK_MODE)
MOCK_REPORT_GENERATION = _env_flag("MOCK_REPORT_GENERATION", default=MOCK_MODE)

# How many requests a single client (identified by IP address) can make
# per time window. Format is "<count>/<period>", e.g. "5/minute" - see
# https://limits.readthedocs.io/en/stable/quickstart.html#rate-limit-string-notation
#
# POST /shops gets a tighter limit since it triggers real transcription +
# Claude API costs once MOCK_MODE is off. GET /shops/{id} is just a cheap
# database read, so it gets a much more generous one.
RATE_LIMIT_CREATE_SHOP = os.environ.get("RATE_LIMIT_CREATE_SHOP") or "5/minute"
RATE_LIMIT_GET_SHOP = os.environ.get("RATE_LIMIT_GET_SHOP") or "60/minute"

# When False (the default), the uploaded audio file is deleted from disk
# once its job finishes processing (success or failure) - see worker.py.
# This is what makes "we don't store your audio long-term" an actual
# fact about this system, not just a claim - the file exists only for as
# long as it takes to transcribe it. Set to True to keep files around
# instead (e.g. for debugging, or handling a dispute about a specific
# shop) - understand that doing so extends how long you're holding
# potentially sensitive recordings, with the data-privacy tradeoffs
# (e.g. under CCPA) that come with that.
RETAIN_AUDIO_FILES = _env_flag("RETAIN_AUDIO_FILES", default=False)

# --- Agent features (see app/support_agent.py and _generate_report_real()
# in worker.py) ---
#
# Both use Anthropic's "Tool Runner" (client.beta.messages.tool_runner) -
# it hands Claude one or more Python functions as tools and automatically
# loops "Claude calls a tool -> we run it -> feed the result back" until
# Claude gives a final answer with no more tool calls. This runs on your
# own server (no separate hosted environment to set up or pay for).

# Report generation (worker.py): instead of one prompt -> one report,
# Claude now works through the guidelines point-by-point first (calling a
# record_finding tool once per requirement), then writes the narrative
# report referencing those findings. Same MOCK_REPORT_GENERATION flag
# above controls whether this runs for real or returns canned mock data.

# Support agent (support_agent.py): a POST /support/ask endpoint that
# answers plain-English questions about your job history (e.g. "how many
# CA shops failed compliance this month") by giving Claude a read-only
# SQL tool against the jobs database. Defaults to whatever MOCK_MODE is,
# same pattern as MOCK_TRANSCRIPTION/MOCK_REPORT_GENERATION above - set
# it separately in .env if you want it to behave differently.
MOCK_SUPPORT_AGENT = _env_flag("MOCK_SUPPORT_AGENT", default=MOCK_MODE)

# Caps how many tool-call round-trips either agent feature can make
# before being forced to stop and answer with whatever it has - a
# safety net against a runaway loop running up your API bill on a single
# request. 8 is generous for both use cases here (a typical guidelines
# list has well under 8 requirements; a typical support question needs
# 1-2 queries).
AGENT_MAX_ITERATIONS = int(os.environ.get("AGENT_MAX_ITERATIONS") or 8)

# Rate limit for POST /support/ask - same reasoning as RATE_LIMIT_CREATE_SHOP
# above: each real request costs real Claude API usage, so it's tighter
# than a plain database read would need.
RATE_LIMIT_SUPPORT_AGENT = os.environ.get("RATE_LIMIT_SUPPORT_AGENT") or "10/minute"
