"""
worker.py
---------
This is where the actual "slow work" happens: transcribing the audio,
then feeding the transcript + guidelines into your Claude skill to get
the final report. It runs in the background, AFTER we've already
responded to the client with a job_id.

This function is intentionally written as a plain async function so
FastAPI's BackgroundTasks can call it directly. If you later switch to
Celery/Redis for scaling, this is the function you'd move into a task.

MOCK_MODE (see config.py)
--------------------------
transcribe_audio() checks config.MOCK_TRANSCRIPTION and generate_report()
checks config.MOCK_REPORT_GENERATION - both default to config.MOCK_MODE,
so setting MOCK_MODE=false in .env turns BOTH real services on at once.
Each can also be overridden independently (see config.py) - handy while
you've wired up one real service but not the other yet, so you're not
forced to build both before either one works.
"""

import asyncio
import os
import textwrap

import assemblyai as aai
from anthropic import AsyncAnthropic, beta_async_tool

from app.jobs import update_job
from app.models import JobStatus
from app.config import (
    MOCK_TRANSCRIPTION,
    MOCK_REPORT_GENERATION,
    ASSEMBLYAI_API_KEY,
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    RETAIN_AUDIO_FILES,
    AGENT_MAX_ITERATIONS,
)


async def process_shop_job(job_id: str, audio_path: str, guidelines_text: str) -> None:
    """
    Runs the full pipeline for one mystery-shop job:
      1. Transcribe the audio file
      2. Run your Claude skill on (transcript + guidelines)
      3. Save the resulting report to the job record

    Any failure along the way marks the job as FAILED with a message,
    instead of leaving it stuck on "pending" forever.
    """
    try:
        update_job(job_id, status=JobStatus.TRANSCRIBING)
        transcript = await transcribe_audio(audio_path)
        update_job(job_id, transcript=transcript)

        update_job(job_id, status=JobStatus.GENERATING_REPORT)
        report = await generate_report(transcript, guidelines_text)

        update_job(job_id, status=JobStatus.COMPLETE, report=report)

    except Exception as e:
        # Catching broadly here is okay at the top level of a background
        # job - we want ANY failure to update job status, not crash silently.
        update_job(job_id, status=JobStatus.FAILED, error_message=str(e))

    finally:
        # Runs whether the job succeeded or failed. By default the audio
        # file is deleted here - it existed only for as long as it took
        # to transcribe it, not indefinitely. See RETAIN_AUDIO_FILES in
        # config.py to keep files instead.
        if not RETAIN_AUDIO_FILES:
            _delete_audio_file(audio_path)


def _delete_audio_file(audio_path: str) -> None:
    """Best-effort cleanup - a failed delete shouldn't crash the job, which has already recorded its own outcome by this point."""
    try:
        if os.path.exists(audio_path):
            os.remove(audio_path)
    except OSError:
        pass


async def transcribe_audio(audio_path: str) -> str:
    """
    Turns the saved audio file into a text transcript.

    When MOCK_TRANSCRIPTION is on, returns a canned transcript
    immediately - no network call, no cost. Otherwise, falls through to
    _transcribe_audio_real(), which calls AssemblyAI.
    """
    if MOCK_TRANSCRIPTION:
        return await _mock_transcribe_audio(audio_path)
    return await _transcribe_audio_real(audio_path)


async def generate_report(transcript: str, guidelines_text: str) -> str:
    """
    Turns a transcript + the shop's guidelines into a finished report.

    When MOCK_REPORT_GENERATION is on, returns a canned 3-5 paragraph
    report immediately - no network call, no cost. Otherwise, falls
    through to _generate_report_real(), which is where you plug in your
    Claude skill.
    """
    if MOCK_REPORT_GENERATION:
        return await _mock_generate_report(transcript, guidelines_text)
    return await _generate_report_real(transcript, guidelines_text)


# ---------------------------------------------------------------------
# Mock implementations - realistic fake data, zero cost.
# ---------------------------------------------------------------------

async def _mock_transcribe_audio(audio_path: str) -> str:
    """
    Fake transcription. Reads the file size off disk (so it at least
    reacts to what was actually uploaded) and returns a canned but
    plausible mystery-shop transcript.

    A tiny asyncio.sleep() simulates the fact that a real transcription
    call would take a moment - handy for confirming your UI actually
    shows the "transcribing" status rather than skipping past it.
    """
    await asyncio.sleep(0.1)

    file_size = os.path.getsize(audio_path) if os.path.exists(audio_path) else 0

    return (
        "[MOCK TRANSCRIPT - generated because MOCK_MODE is on] "
        f"(source audio: {file_size} bytes) "
        "Hi, welcome in. Let me know if you need anything. "
        "I'm looking for a birthday gift for my sister, she's into hiking. "
        "Sure, we've got a few options over here - this jacket's popular, "
        "and we've got trail shoes on sale too. Do you have a budget in mind? "
        "Around eighty dollars. Okay, this jacket's seventy-five, "
        "that should work great. Do you want me to ring that up, or would "
        "you like a minute to look around first? I'll look around a bit more, "
        "thanks. No problem, just wave me down if you need anything, "
        "I'll be up front."
    )


async def _mock_generate_report(transcript: str, guidelines_text: str) -> str:
    """
    Fake report generation. Builds a realistic 3-5 paragraph report out
    of the transcript and guidelines it was given, so the output visibly
    reflects the actual inputs (not just static lorem ipsum) without
    ever calling Claude.
    """
    await asyncio.sleep(0.1)

    guideline_preview = guidelines_text.strip().splitlines()[0] if guidelines_text.strip() else "no specific guidelines were provided"

    return (
        "[MOCK REPORT - generated because MOCK_MODE is on]\n\n"
        "Upon entering the store, I was greeted within approximately thirty "
        "seconds by a staff member who offered assistance in a friendly, "
        "unhurried tone. I explained that I was shopping for a birthday gift "
        "for my sister, who enjoys hiking, and the associate asked a "
        "reasonable follow-up question about my budget before making a "
        "recommendation.\n\n"
        "The associate suggested a jacket priced within my stated budget and "
        "mentioned a complementary sale item (trail shoes) without being "
        "pushy about the upsell. Product knowledge appeared solid - the "
        "recommendation was relevant to the stated need rather than a "
        "generic suggestion.\n\n"
        "When I indicated I wanted to keep browsing rather than purchase "
        "immediately, the associate respected that without applying "
        "pressure, and let me know they would be available up front if I "
        "had further questions. This matches the guidelines' expectation "
        f"that greeters not be pushy ({guideline_preview!r} was the first "
        "guideline on file for this shop).\n\n"
        "Overall, this was a positive interaction: a prompt greeting, "
        "relevant product recommendations tailored to my stated budget and "
        "need, and no pressure tactics when I chose not to buy immediately. "
        "I would recommend no corrective action based on this visit; the "
        "associate's approach reflected well on the store."
    )


# ---------------------------------------------------------------------
# Real implementations - fill these in when you're ready to go live.
# ---------------------------------------------------------------------

async def _transcribe_audio_real(audio_path: str) -> str:
    """
    Transcribes the audio file using AssemblyAI.

    AssemblyAI's Python SDK does two things under the hood: uploads the
    audio file, then polls their servers until transcription finishes -
    transcriber.transcribe() blocks until that whole process is done
    (usually well under a minute for a typical mystery-shop recording).

    Because it's a blocking (synchronous) call, we run it in a separate
    thread with asyncio.to_thread() instead of calling it directly. If
    we called it directly, it would freeze this whole server - including
    every other request - for as long as the transcription takes.
    """
    if not ASSEMBLYAI_API_KEY:
        raise RuntimeError(
            "ASSEMBLYAI_API_KEY is not set. Get a free key at "
            "https://www.assemblyai.com/ and add it to your .env file, "
            "or set MOCK_TRANSCRIPTION=true to skip real transcription."
        )

    aai.settings.api_key = ASSEMBLYAI_API_KEY
    transcriber = aai.Transcriber()

    transcript = await asyncio.to_thread(transcriber.transcribe, audio_path)

    if transcript.status == aai.TranscriptStatus.error:
        # AssemblyAI reports failures (e.g. corrupt audio, unsupported
        # format) as a status on the result rather than raising an
        # exception itself, so we raise one ourselves here - that's what
        # lets process_shop_job()'s try/except mark the job FAILED.
        raise RuntimeError(f"AssemblyAI transcription failed: {transcript.error}")

    return transcript.text


# The instructions Claude follows when writing the report. This is the
# "skill" in prompt form: it encodes what makes a good mystery-shop
# narrative (plain prose, specific over generic, flag gaps instead of
# guessing) so any client's guidelines_text - not just one specific
# shop program - produces a report in the same reliable style.
#
# This now describes a TWO-PHASE process (analyze via tool calls, then
# write) rather than "just write the report" - see
# _generate_report_real() below for why: a single prompt->response call
# tends to skim long or complex guideline lists, while being forced to
# record a finding per requirement first makes it address each one
# explicitly before it ever starts writing prose.
REPORT_SYSTEM_PROMPT = textwrap.dedent(
    """\
    You are an experienced mystery shopping report writer. You'll be
    given a transcript of an audio recording from a mystery shop visit,
    along with the guidelines the shopper was asked to evaluate against.
    Your job is to write the narrative report a client (a retailer,
    property manager, or mystery shopping company) will read to judge
    how the visit went.

    Work in two phases:

    PHASE 1 - Analyze first. Identify each distinct, checkable
    requirement in the guidelines (e.g. "greet within 30 seconds",
    "mention the current promotion", "don't be pushy about upselling").
    For EACH ONE, call the record_finding tool with your assessment,
    citing a specific quote or close paraphrase from the transcript as
    evidence. Do this for every requirement before moving on - don't
    skip straight to writing.

    PHASE 2 - Once you've recorded a finding for every requirement,
    write the final narrative report as your plain-text reply (no more
    tool calls). Base it on the findings you just recorded, not a fresh
    read of the transcript - the findings ARE your working notes.

    Report format: 3-5 paragraphs of plain prose - no bullet points, no
    headers, no bolded labels. Structure it roughly as:
    - Opening: the setting, who was involved, and the shopper's initial
      impression (how quickly they were greeted, the tone, etc.)
    - Middle paragraph(s): walk through what happened relative to the
      SPECIFIC guidelines provided, addressing each requirement using
      the findings you recorded - specific details, not generic summary
    - Closing: how the visit wrapped up and an overall assessment of
      performance against the guidelines

    If a finding says the transcript doesn't make something clear, say
    so explicitly in the report (e.g. "The recording does not make
    clear whether X occurred") instead of guessing. This is a paid
    deliverable - accuracy matters more than completeness.

    Output only the report narrative itself in your final reply - no
    title, no headers, no preamble like "Here is the report."
    """
)


async def _generate_report_real(transcript: str, guidelines_text: str) -> str:
    """
    Generates the report using Claude, in two steps instead of one
    prompt -> one response call:

      1. Claude reads the guidelines and transcript, and calls
         record_finding once per distinct requirement it identifies - a
         structured pass through the guidelines before it writes anything.
      2. Once every requirement has a recorded finding, Claude writes the
         final narrative report referencing those findings.

    This uses Anthropic's Tool Runner (client.beta.messages.tool_runner),
    which automates that "call a tool -> see the result -> decide what's
    next" loop instead of you writing it by hand - see
    app/support_agent.py's module docstring for more on how Tool Runner
    works and why it (rather than a separate hosted agent environment)
    is the right fit for a step like this.

    Uses AsyncAnthropic (not the sync Anthropic client) so this await
    doesn't block the server while waiting on the API response.
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file, "
            "or set MOCK_REPORT_GENERATION=true to skip real report generation."
        )

    # A fresh list PER CALL, not a module-level one - this function can
    # run for multiple jobs concurrently (FastAPI background tasks), and
    # a shared list would mix findings from different shops together.
    findings = []

    @beta_async_tool
    async def record_finding(requirement: str, met: bool, evidence: str) -> str:
        """Record whether one specific guideline requirement was met, with supporting evidence from the transcript.

        Call this once for EACH distinct requirement in the guidelines before writing the final report.

        Args:
            requirement: The specific guideline requirement being evaluated, in your own words.
            met: Whether the transcript shows this requirement was satisfied.
            evidence: A specific quote or close paraphrase from the transcript supporting your assessment. If the transcript doesn't make it clear, say so here instead of guessing.
        """
        findings.append({"requirement": requirement, "met": met, "evidence": evidence})
        return "Recorded."

    client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    runner = client.beta.messages.tool_runner(
        model=ANTHROPIC_MODEL,
        max_tokens=1536,
        max_iterations=AGENT_MAX_ITERATIONS,
        system=REPORT_SYSTEM_PROMPT,
        tools=[record_finding],
        messages=[
            {
                "role": "user",
                "content": (
                    f"GUIDELINES FOR THIS SHOP:\n{guidelines_text}\n\n"
                    f"TRANSCRIPT OF THE VISIT:\n{transcript}\n\n"
                    "Follow the two-phase process: record a finding for "
                    "every requirement, then write the narrative report."
                ),
            }
        ],
    )
    final_message = await runner.until_done()

    # final_message.content is a list of content blocks. Once Claude
    # stops calling tools, its answer is the text block(s) - joining all
    # of them is a bit more robust than assuming there's exactly one.
    report_text = "".join(
        block.text for block in final_message.content if getattr(block, "type", None) == "text"
    ).strip()

    if not report_text:
        # Can happen if the agent hit AGENT_MAX_ITERATIONS mid-tool-call,
        # without ever writing the narrative - surface that as a clear
        # failure (worker.py's caller marks the job FAILED) instead of
        # silently completing with an empty report.
        raise RuntimeError(
            f"Report generation didn't produce a final report after recording "
            f"{len(findings)} finding(s) - it may have hit the tool-call limit "
            f"(AGENT_MAX_ITERATIONS={AGENT_MAX_ITERATIONS}) before writing the narrative."
        )

    return report_text
