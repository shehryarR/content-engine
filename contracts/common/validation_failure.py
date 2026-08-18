"""
contracts/common/validation_failure.py

Pydantic v2 model for a stage's validation failure.

Distinct from ValidationReportV1 (contracts/common/envelope.py): a
ValidationReportV1 is the routine pass/fail record every stage produces.
A ValidationFailureV1 is what gets raised (wrapped in a non-retryable
Temporal ApplicationError) when a stage's output fails validation in a
way pipeline.py should react to directly — e.g. by retrying that stage
locally — rather than leaving it to Temporal's transport-level retry
policy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from contracts.common.envelope import ArtifactRefV1


class ValidationFailureV1(BaseModel):
    """
    Records why a stage's output failed validation.
    """

    stage_id: str = Field(
        ..., description="Stage/gate whose validation failed, e.g. 'S60'."
    )
    failure_type: str = Field(
        ...,
        description="Machine-readable failure category, e.g. 'hash_mismatch'. "
        "This is the failure_code the M3 roadmap refers to - kept as "
        "failure_type since that name is already threaded through "
        "ValidationReportV1, RETRYABLE_VALIDATION_FAILURE_TYPES, and every "
        "stage validator; renaming it would be a larger, purely cosmetic "
        "change touching every validator for no behavioral gain.",
    )
    message: str = Field(
        ..., description="Human-readable description of the failure."
    )
    feedback_context: Optional[str] = Field(
        default=None,
        description="Context to carry into a retried attempt of this stage, "
        "e.g. what specifically went wrong, so the provider can correct for "
        "it on the next attempt.",
    )
    failed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when this validation failure was recorded.",
    )
    failed_field: Optional[str] = Field(
        default=None,
        description="Which specific field/check failed, e.g. 'scenes[1]' or "
        "'duration'. None when the failure isn't localized to one field "
        "(e.g. a corrupt/unreadable file).",
    )
    evidence_ref: Optional[ArtifactRefV1] = Field(
        default=None,
        description="Pointer to the stored ValidationReportV1 (or offending "
        "artifact) backing this failure - makes the failure inspectable "
        "later instead of living only in a log line.",
    )
    retryable: bool = Field(
        default=False,
        description="Whether this failure_type is in "
        "RETRYABLE_VALIDATION_FAILURE_TYPES at the time this record was "
        "raised. Duplicated onto the record itself (rather than only "
        "computed later in pipeline.py) so a failure is self-describing "
        "without cross-referencing pipeline.py's set.",
    )
    suggested_fix: Optional[str] = Field(
        default=None,
        description="Short, validator-authored hint for a human reading a "
        "failed run later. Distinct from feedback_context, which is prose "
        "meant to go back into a retried LLM prompt, not for a person.",
    )

    model_config = {
        "extra": "forbid",
    }