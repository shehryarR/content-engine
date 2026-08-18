"""
contracts/stages/s70_qc.py

Stage schema for S70 quality control.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError


class QualityReportV1(BaseModel):
    """QC result for a MasterVideoV1 - feeds into G80 approval."""

    run_id: str = Field(..., description="Which run this QC pass belongs to")
    master_video_hash: str = Field(
        ...,
        min_length=64,
        max_length=64,
        description="SHA-256 of the MasterVideoV1 this report evaluates",
    )
    passed: bool = Field(..., description="Whether QC passed")
    metrics: dict[str, float] = Field(
        default_factory=dict,
        description="Named metric scores, e.g. identity_similarity, sync_score",
    )

    # Subjective quality check (M3 Day 3, item 4) - separate from the
    # deterministic Layer 1/4 metrics above. None when the model judge
    # wasn't run or errored out (e.g. missing API key) - a skipped
    # subjective check is distinct from a failed one and must not be
    # conflated with False.
    subjective_quality_passed: bool | None = Field(
        default=None,
        description="Model judge's pass/fail on presentation quality. "
        "None if the check wasn't run.",
    )
    subjective_quality_confidence: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="Model judge's confidence in its own verdict.",
    )
    subjective_quality_rationale: str | None = Field(
        default=None,
        description="Short model-authored explanation for the verdict.",
    )

    model_config = {"extra": "forbid"}


if __name__ == "__main__":
    qc_report = QualityReportV1(
        run_id="run_001",
        master_video_hash="a" * 64,
        passed=True,
        metrics={"identity_similarity": 0.95, "sync_score": 0.98},
    )
    print(qc_report.model_dump_json(indent=2))

    # Test with deliberately invalid input
    try:
        QualityReportV1(
            run_id="run_001",
            master_video_hash="short_hash",
            passed=True,
        )
        raise RuntimeError("Validation failed to reject invalid master_video_hash!")
    except ValidationError:
        print("Successfully caught invalid master_video_hash")
