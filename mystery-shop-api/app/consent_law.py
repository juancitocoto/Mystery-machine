"""
consent_law.py
---------------
Recording-consent classification for US states, used to gate POST /shops
so a job can't be created (and audio can't even be saved to disk) unless
the request establishes a lawful basis to have recorded the conversation
in the first place.

READ THIS BEFORE RELYING ON THIS FILE - IT IS NOT LEGAL ADVICE
----------------------------------------------------------------
Wiretapping/eavesdropping consent law varies by state and is genuinely
contested in places - reputable sources disagree with each other about
which states require all-party consent, and several states apply
DIFFERENT rules depending on the recording's medium (phone call vs.
in-person conversation) or location (public area vs. a place with a
reasonable expectation of privacy). This file - written by Claude, which
is not a lawyer - does not attempt to resolve every dispute on its own
authority. It encodes the two-pass research done for this project (see
Sources below) as a starting point, and fails closed - blocking
processing - for anything neither pass was confident about.

Before using this in production, have an actual attorney (ideally one
who handles recording/wiretapping law) verify every entry below,
including the ones already filled in, and keep this file updated: state
statutes and case law both change, and this table is a compliance aid,
not a finished legal determination.

Two orthogonal safety gates, both enforced in check_consent_basis():

1. STATE + RECORDING MEDIUM -> is one-party or all-party consent
   required? Most states don't distinguish by medium, but Connecticut
   and Oregon do (see MEDIUM_SPECIFIC_OVERRIDES) - a state-only lookup
   would get those two wrong depending on what's actually being
   recorded, so recording_medium is a required field, not an
   afterthought.

2. RECORDING LOCATION TYPE -> was this recorded somewhere with a
   reasonable expectation of privacy (a break room, a manager's office,
   a fitting room, a restroom)? If so, it's blocked UNCONDITIONALLY,
   regardless of state or any consent attested - a legitimate mystery
   shop has no business recording in those spaces anyway, and this is
   deliberately stricter than what some states might technically permit
   with full all-party consent.

Sources consulted (accessed 2026-08 - re-verify before relying on this,
laws and case law change):
- https://www.recordinglaw.com/party-two-party-consent-states/
- A second, more complete state-by-state pass supplied directly by the
  user for this project, itself citing https://www.getnextphone.com,
  https://convertaudiototext.com, https://www.bestmark.com,
  https://www.justia.com, and others.
- Both passes independently flagged Michigan as contested (statute reads
  all-party; courts have read in a participant exception since a 1982
  case, applied inconsistently) - that's why it's REQUIRES_REVIEW below
  rather than ONE_PARTY, even though several list-style sources include
  it as one-party without the caveat.
"""

from enum import Enum
from typing import Optional

from fastapi import HTTPException


class ConsentRequirement(str, Enum):
    ONE_PARTY = "one_party"              # the shopper's own consent is enough
    ALL_PARTY = "all_party"              # every party (incl. the employee) must consent
    REQUIRES_REVIEW = "requires_review"  # not yet verified here - blocks processing


class RecordingMedium(str, Enum):
    IN_PERSON = "in_person"
    PHONE_CALL = "phone_call"


class RecordingLocationType(str, Enum):
    PUBLIC_AREA = "public_area"    # sales floor, showroom, checkout, leasing office lobby - anyone nearby could overhear
    PRIVATE_AREA = "private_area"  # break room, manager's office, fitting room, restroom - always blocked, see module docstring


# All 50 states + DC. Used to tell "not a real state code" (typo, wrong
# country, etc.) apart from "a real state we haven't classified yet" -
# those two cases get different error messages in main.py.
US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
}

# Same 51 codes as USState.value.value pairs (functional Enum API, built
# FROM US_STATE_CODES above so the two can't drift apart) - main.py uses
# this as the shop_state Form(...) type instead of a plain str, so /docs
# renders it as a dropdown of real state codes instead of a free-text
# box a user could mistype. This also means FastAPI itself now rejects
# a garbage code like "ZZ" before the request ever reaches this file's
# check_consent_basis() - get_requirement()'s "unrecognized code" path
# below still exists as a fallback for any caller that passes a state
# code some other way (e.g. a direct function call, not through the API).
USState = Enum("USState", {code: code for code in sorted(US_STATE_CODES)}, type=str)

# Base classification per state - used as-is UNLESS the state also
# appears in MEDIUM_SPECIFIC_OVERRIDES below (currently CT and OR).
# Any state/DC code not listed here defaults to REQUIRES_REVIEW.
STATE_CONSENT_REQUIREMENTS = {
    # --- All-party consent (12 states). Every party, including the
    # employee/agent being shopped, must consent. A shop from one of
    # these additionally needs employer_disclosure_attested=true - i.e.
    # confirmation the location has an active employee monitoring/
    # recording disclosure in place (see check_consent_basis()). ---
    "CA": ConsentRequirement.ALL_PARTY,
    "CT": ConsentRequirement.ALL_PARTY,  # base value; overridden by medium below
    "DE": ConsentRequirement.ALL_PARTY,
    "FL": ConsentRequirement.ALL_PARTY,
    "IL": ConsentRequirement.ALL_PARTY,
    "MD": ConsentRequirement.ALL_PARTY,
    "MA": ConsentRequirement.ALL_PARTY,
    "MT": ConsentRequirement.ALL_PARTY,
    "NV": ConsentRequirement.ALL_PARTY,
    "NH": ConsentRequirement.ALL_PARTY,
    "PA": ConsentRequirement.ALL_PARTY,
    "WA": ConsentRequirement.ALL_PARTY,

    # --- One-party consent (38 states + DC). The shopper's own consent
    # (as a party to the conversation) is enough. ---
    "AL": ConsentRequirement.ONE_PARTY,
    "AK": ConsentRequirement.ONE_PARTY,
    "AZ": ConsentRequirement.ONE_PARTY,
    "AR": ConsentRequirement.ONE_PARTY,
    "CO": ConsentRequirement.ONE_PARTY,
    "GA": ConsentRequirement.ONE_PARTY,
    "HI": ConsentRequirement.ONE_PARTY,  # the "private area" exception noted in research is handled by the universal RecordingLocationType gate, not a special case here
    "ID": ConsentRequirement.ONE_PARTY,
    "IN": ConsentRequirement.ONE_PARTY,
    "IA": ConsentRequirement.ONE_PARTY,
    "KS": ConsentRequirement.ONE_PARTY,
    "KY": ConsentRequirement.ONE_PARTY,
    "LA": ConsentRequirement.ONE_PARTY,
    "ME": ConsentRequirement.ONE_PARTY,
    # MI intentionally omitted - see module docstring. Both research
    # passes flagged it as contested; defaults to REQUIRES_REVIEW.
    "MN": ConsentRequirement.ONE_PARTY,
    "MS": ConsentRequirement.ONE_PARTY,
    "MO": ConsentRequirement.ONE_PARTY,
    "NE": ConsentRequirement.ONE_PARTY,
    "NJ": ConsentRequirement.ONE_PARTY,
    "NM": ConsentRequirement.ONE_PARTY,
    "NY": ConsentRequirement.ONE_PARTY,
    "NC": ConsentRequirement.ONE_PARTY,
    "ND": ConsentRequirement.ONE_PARTY,
    "OH": ConsentRequirement.ONE_PARTY,
    "OK": ConsentRequirement.ONE_PARTY,
    "OR": ConsentRequirement.ONE_PARTY,  # base value; overridden by medium below
    "RI": ConsentRequirement.ONE_PARTY,
    "SC": ConsentRequirement.ONE_PARTY,
    "SD": ConsentRequirement.ONE_PARTY,
    "TN": ConsentRequirement.ONE_PARTY,
    "TX": ConsentRequirement.ONE_PARTY,
    "UT": ConsentRequirement.ONE_PARTY,
    "VT": ConsentRequirement.ONE_PARTY,
    "VA": ConsentRequirement.ONE_PARTY,
    "WV": ConsentRequirement.ONE_PARTY,
    "WI": ConsentRequirement.ONE_PARTY,
    "WY": ConsentRequirement.ONE_PARTY,
    "DC": ConsentRequirement.ONE_PARTY,

    # MI is the only state/DC code deliberately left unlisted - every
    # other one is classified above. An unlisted code still falls back
    # to REQUIRES_REVIEW via get_requirement(), same as MI.
}

# States where the required consent level depends on HOW the
# conversation was recorded, not just where. Checked before falling
# back to STATE_CONSENT_REQUIREMENTS.
MEDIUM_SPECIFIC_OVERRIDES = {
    "CT": {
        RecordingMedium.PHONE_CALL: ConsentRequirement.ALL_PARTY,
        RecordingMedium.IN_PERSON: ConsentRequirement.ONE_PARTY,
    },
    "OR": {
        RecordingMedium.PHONE_CALL: ConsentRequirement.ONE_PARTY,
        RecordingMedium.IN_PERSON: ConsentRequirement.ALL_PARTY,
    },
}


def get_requirement(state_code: str, recording_medium: RecordingMedium) -> Optional[ConsentRequirement]:
    """
    Returns the consent requirement for a state code + recording medium
    (case-insensitive, whitespace-tolerant on the state code), or None
    if state_code isn't a recognized US state/DC code at all - distinct
    from "recognized but not yet classified", which returns
    REQUIRES_REVIEW instead of None.
    """
    code = (state_code or "").strip().upper()
    if code not in US_STATE_CODES:
        return None

    if code in MEDIUM_SPECIFIC_OVERRIDES:
        return MEDIUM_SPECIFIC_OVERRIDES[code][recording_medium]

    return STATE_CONSENT_REQUIREMENTS.get(code, ConsentRequirement.REQUIRES_REVIEW)


def check_consent_basis(
    state_code: str,
    recording_medium: RecordingMedium,
    recording_location_type: RecordingLocationType,
    consent_attested: bool,
    employer_disclosure_attested: bool,
) -> ConsentRequirement:
    """
    The actual gate: raises HTTPException (422) if the request doesn't
    establish a lawful basis to process this recording. Returns the
    ConsentRequirement that applied on success, so the caller can
    persist it on the job record as part of the audit trail.

    Called from main.py BEFORE the audio file is saved to disk or a job
    is created - a request that fails this check leaves no trace on the
    server at all, which is the point: this isn't just a warning, it's
    a hard stop.
    """
    # Normalize once, up front, to a PLAIN string - state_code may arrive
    # as a USState enum member (main.py's shop_state Form field) rather
    # than a plain str. Enum members mixed with str behave like strings
    # for .strip()/.upper() (which is what get_requirement() below relies
    # on), but an f-string like f"{state_code}" on a raw enum member
    # prints "USState.TX" instead of "TX" - ugly in an error message a
    # human reads. Using this normalized plain string everywhere below
    # (not the original state_code parameter) avoids that regardless of
    # what type the caller passed in.
    state_code = (state_code or "").strip().upper()

    # Gate 1: location. Unconditional, regardless of state or consent -
    # see module docstring for why this doesn't have a state-by-state
    # exception path.
    if recording_location_type == RecordingLocationType.PRIVATE_AREA:
        raise HTTPException(
            status_code=422,
            detail=(
                "Recording in a private area (break room, manager's office, "
                "fitting room, restroom, etc.) is not permitted through this "
                "API under any circumstances, regardless of state or consent. "
                "Only recordings from public-facing areas (sales floor, "
                "showroom, checkout, leasing office lobby, etc.) can be "
                "submitted - set recording_location_type=public_area."
            ),
        )

    # Gate 2: state + medium. Distinct "not a real state" vs. "not yet
    # classified" error messages, since they call for different fixes.
    requirement = get_requirement(state_code, recording_medium)

    if requirement is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"'{state_code}' is not a recognized US state or DC code. "
                "Use the two-letter postal abbreviation, e.g. 'TX'."
            ),
        )

    if requirement == ConsentRequirement.REQUIRES_REVIEW:
        raise HTTPException(
            status_code=422,
            detail=(
                f"This system hasn't confirmed {state_code}'s recording consent "
                f"requirement for {recording_medium.value} recordings yet, so a "
                "job can't be created for it. Verify the actual law for this "
                "state (ideally with an attorney) and add it to "
                "STATE_CONSENT_REQUIREMENTS (or MEDIUM_SPECIFIC_OVERRIDES) in "
                "app/consent_law.py before submitting shops like this one."
            ),
        )

    # Gate 3: the shopper's own consent. Required unconditionally - even
    # in a one-party state, someone has to actually confirm the shopper
    # (a party to the conversation) consented to recording it.
    if not consent_attested:
        raise HTTPException(
            status_code=422,
            detail=(
                "consent_attested must be true: confirm the shopper consented "
                "to being part of the recorded conversation before submitting."
            ),
        )

    # Gate 4: all-party states additionally need proof of a lawful basis
    # for recording the OTHER party (the employee/agent) - typically an
    # employer's active monitoring/recording disclosure policy.
    if requirement == ConsentRequirement.ALL_PARTY and not employer_disclosure_attested:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{state_code} requires all-party consent to record "
                f"{recording_medium.value} conversations. Recording here is "
                "only lawful if the location has an active employee "
                "monitoring/recording disclosure in place (e.g. an employee "
                "handbook clause covering QA recording) - confirm that's the "
                "case and set employer_disclosure_attested=true, or don't "
                "submit this shop."
            ),
        )

    return requirement
