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


class ValidationFailureV1(BaseModel):
    """
    Records why a stage's output failed validation.
    """

    stage_id: str = Field(
        ..., description="Stage/gate whose validation failed, e.g. 'S60'."
    )
    failure_type: str = Field(
        ...,
        description="Machine-readable failure category, e.g. 'hash_mismatch'.",
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

    model_config = {
        "extra": "forbid",
    }
