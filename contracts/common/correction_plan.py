"""
contracts/common/correction_plan.py

Pydantic v2 model for the retry decision pipeline.py makes after catching a
ValidationFailureV1 for a stage.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

# Sane default for how many local correction retries a stage gets before
# pipeline.py gives up and lets the failure propagate. Per-stage tuning is
# out of scope today.
DEFAULT_RETRY_BUDGET = 3


class CorrectionPlanV1(BaseModel):
    """
    Decision record for whether/how to retry a stage after a
    ValidationFailureV1.
    """

    stage_id: str = Field(
        ..., description="Stage/gate this correction plan applies to."
    )
    retryable: bool = Field(
        ..., description="Whether this failure is worth retrying locally."
    )
    feedback_context: Optional[str] = Field(
        default=None,
        description="Context carried from the ValidationFailureV1 into the "
        "retried attempt.",
    )
    retry_budget: int = Field(
        default=DEFAULT_RETRY_BUDGET,
        ge=0,
        description="Max number of local correction retries for this stage.",
    )

    model_config = {
        "extra": "forbid",
    }
