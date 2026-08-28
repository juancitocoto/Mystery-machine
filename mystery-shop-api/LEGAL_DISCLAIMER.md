# Legal disclaimer draft — NOT legal advice

For AlloMetrics (Allophilism Connects).

**This file is a starting point for a conversation with an actual attorney, not something to paste into a contract or a public-facing terms page as-is.** Claude (which wrote this) is not a lawyer, isn't licensed to practice law anywhere, and has no way to know your specific business structure, the states you operate in, or how courts in those states have actually applied these statutes recently. Have this reviewed by an attorney - ideally one with media/privacy or wiretapping experience - before you rely on it, show it to a client, or put it in front of a user as a binding term.

## What this API's technical controls actually do

`app/consent_law.py` and the checks in `app/main.py`'s `POST /shops` endpoint do two things:

1. Require every submission to declare the state, recording medium (in-person vs. phone), and location type (public vs. private area) the recording came from, plus an explicit attestation that the shopper consented and — in all-party-consent states — that the employer has an active recording/monitoring disclosure in place.
2. Reject the request (before the audio file is ever saved to disk) if that information doesn't establish a lawful basis to have made the recording, based on a classification table that is **intentionally incomplete** — most US states default to "requires review" and are blocked until someone manually verifies and adds them.

This reduces risk and creates an audit trail. **It does not guarantee legal compliance.** In particular:

- The state classification table needs attorney verification, including the entries already filled in — see the sources cited in `app/consent_law.py` and the note that two independent research passes still disagreed on several states (Michigan is intentionally left unresolved for exactly this reason).
- It can't verify that an attestation is *true* — a client could check "consent_attested: true" without it actually being true. The system creates a record of what was attested, not proof of what actually happened.
- It only covers the legal question of recording consent. It says nothing about other laws that might apply (state mini-wiretap statutes with different definitions, employment law, contract law between you and your clients, data privacy law like CCPA for how you handle the resulting recordings and reports, etc.).
- Laws and case law change. This table reflects research as of August 2026 and needs periodic re-verification, not a one-time review.

## Draft terms-of-service clause (for attorney review)

A starting point for the kind of clause described in the research that prompted this feature — pushing responsibility for lawful collection onto the client submitting the recording, since your API processes audio after the fact and doesn't control how or where it was captured:

> **Client Responsibility for Lawful Recording.** Client represents and warrants that any audio recording submitted to the Service was collected in compliance with all applicable federal, state, and local laws, including wiretapping, eavesdropping, and consent-to-record statutes in the jurisdiction where the recording was made. The Service's recording-consent classification features (if any) are provided as a compliance aid only and do not constitute legal advice or a guarantee of lawfulness; Client remains solely responsible for verifying that each submission was lawfully obtained. Client agrees to indemnify and hold harmless Allophilism Connects from any claim, liability, or expense arising from Client's submission of an unlawfully obtained recording.

Things an attorney will likely want to adjust: the scope of the indemnification, whether "Client" should extend to Client's own shoppers/contractors, whether this needs to reference specific statutes, and how it interacts with your actual Terms of Service structure and choice-of-law clause.

## The "processing vault" positioning

The research behind this feature suggested pitching audio handling as a privacy feature: process in memory, don't retain long-term. As of this change, that's now actually true by default — `RETAIN_AUDIO_FILES=false` (the default, see `app/config.py`) deletes each uploaded audio file once its job finishes, whether it succeeded or failed. Before repeating this claim to a client or in marketing material, it's worth having someone confirm: how long "temporary" needs to be to satisfy whatever specific claim you make (immediately after processing vs. some retention window), whether your infrastructure provider (Docker host, Railway/Render/Fly.io, etc.) retains its own disk snapshots or logs that would undercut the claim, and whether CCPA or any other privacy law that applies to you has specific retention or disclosure requirements beyond "don't keep it long."
