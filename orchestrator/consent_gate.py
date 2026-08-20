"""
orchestrator/consent_gate.py

Pre-flight validation for a run's IdeaRequestV1, called before the
Temporal workflow starts. Real registry lookup against
IdentityProfileV1/VoiceProfileV1 rows and their consent grants.
"""

from contracts.stages.idea_request import IdeaRequestV1, Modality
from orchestrator.telemetry import get_connection


def _fetch_one(query: str, params: tuple) -> tuple | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()


def _validate_identity_consent(identity_id: str) -> None:
    row = _fetch_one(
        "SELECT consent_status FROM identity_profiles WHERE identity_id=%s",
        (identity_id,),
    )
    if row is None:
        raise ValueError(f"identity_id {identity_id} not found in registry")
    if row[0] != "active":
        raise ValueError(f"identity_id {identity_id} consent status is {row[0]}, not active")


def _validate_voice_consent(voice_id: str) -> None:
    row = _fetch_one(
        "SELECT consent_status FROM voice_profiles WHERE voice_id=%s",
        (voice_id,),
    )
    if row is None:
        raise ValueError(f"voice_id {voice_id} not found in registry")
    if row[0] != "active":
        raise ValueError(f"voice_id {voice_id} consent status is {row[0]}, not active")


def validate_run(idea: IdeaRequestV1) -> None:
    if idea.modality == Modality.AVATAR:
        if not idea.identity_id:
            raise ValueError("AVATAR run missing identity_id")
        _validate_identity_consent(idea.identity_id)

    if not idea.voice_id:
        raise ValueError("Run missing voice_id")
    _validate_voice_consent(idea.voice_id)


def revalidate_identity(identity_id: str) -> None:
    """
    Re-check an identity's consent status mid-run.

    validate_run only ever runs once, pre-flight, before the workflow
    starts. When S30's correction plan re-fetches the identity reference
    on a retryable failure (M3 Day 1 Part 4), that re-fetch needs its own
    consent check: consent could have been revoked between the original
    pre-flight validate_run() call and this retry, and a retry must not
    silently resubmit a reference whose consent grant no longer holds.
    Raises the same way validate_run does - not retryable, meant to
    propagate and stop the run.
    """
    _validate_identity_consent(identity_id)