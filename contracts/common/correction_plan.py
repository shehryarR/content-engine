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

    target_stage: str = Field(
        ..., description="Stage/gate this correction plan applies to. "
        "(Named target_stage per the M3 roadmap spec - was stage_id.)"
    )
    invalidated_downstream_stages: list[str] = Field(
        default_factory=list,
        description="Stages downstream of target_stage whose already-"
        "produced output is no longer valid and must be re-run once "
        "target_stage succeeds. Always [] today: the pipeline only ever "
        "retries the current stage in place before any downstream stage "
        "has run, so nothing is actually invalidated yet. Kept so the "
        "field exists for M4/M5, where a downstream stage could plausibly "
        "already be complete when an upstream retry happens.",
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
    stop_condition: str = Field(
        default="retry_budget_exhausted",
        description="Human-readable reason retries would stop for this "
        "plan. Only one real stop condition exists today "
        "(retry_budget_exhausted); naming it explicitly means adding a "
        "second one later (e.g. a non-retryable failure_type mid-loop) "
        "doesn't require another contract change.",
    )

    model_config = {
        "extra": "forbid",
    }