"""
contracts/common/envelope.py

Pydantic v2 models for pipeline envelopes, stage outputs, and validation reports.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


# Support structs (required by StageEnvelopeV1's fields)


class ArtifactRefV1(BaseModel):
    """
    A reference to a stored artifact — never the raw bytes themselves.

    Stages pass around pointers (path + hash + mime type). The actual file
    lives in the object store (S3-compatible); the manifest / envelope only
    ever stores the reference.
    """

    artifact_id: str = Field(..., description="Unique ID for this artifact.")
    path: str = Field(
        ...,
        description="Content-addressed storage path/key for the artifact, "
        "e.g. 's3://bucket/artifacts/<sha256>.mp4'. Must not be a mutable "
        "alias such as 'latest.mp4'.",
    )
    hash: str = Field(
        ...,
        description="SHA-256 hex digest of the artifact contents.",
        min_length=64,
        max_length=64,
    )
    mime_type: str = Field(
        ...,
        description="MIME type of the stored artifact, e.g. 'video/mp4', "
        "'audio/wav', 'application/json'.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when this artifact reference was created.",
    )
    model_config = {
        "frozen": True,
        "extra": "forbid",
    }

    

class ProviderDescriptorV1(BaseModel):
    """
    Generic description of whatever implementation handled a stage.

    The pipeline graph never knows concrete vendor names — it only knows
    capabilities (e.g. "voice_provider"). This descriptor is where the
    concrete provider identity is recorded, for telemetry/debugging, without
    leaking that identity into the graph or contracts.
    """

    provider: str = Field(
        ..., description="Capability-level provider identifier, e.g. 'voice_provider'."
    )
    model: str = Field(
        ..., description="Underlying model name used by the provider implementation."
    )
    version: str = Field(
        ..., description="Version/tag of the model or provider implementation."
    )
    capability: str = Field(
        ...,
        description="The abstract capability this provider fulfills, e.g. "
        "'script_generation', 'voice_synthesis', 'avatar_render'.",
    )

    # Added: telemetry fields, matching the deck's cost/latency/provider/version rule
    endpoint: Optional[str] = Field(
        default=None, description="Provider API endpoint or resource identifier, if applicable"
    )
    cost: Optional[float] = Field(
        default=None, description="Cost incurred for this stage execution, if known"
    )
    latency_ms: Optional[int] = Field(
        default=None, description="Execution latency in milliseconds, if known"
    )
    timestamp: Optional[datetime] = Field(
        default=None, description="When this provider was invoked, if known"
    )
    model_config = {"extra": "forbid"}

# Deliverable: StageEnvelopeV1

class StageEnvelopeV1(BaseModel):
    """
    The wrapper every pipeline stage receives and emits.

    Required fields per the runbook:
        - stage_id
        - attempt
        - input hash
        - artifact references
        - validation reference
        - provider descriptor
    """

    stage_id: str = Field(
        ...,
        description="Permanent identifier for the stage/gate, e.g. 'S00', "
        "'S10', ..., 'G80', 'G90', 'S100'.",
    )
    attempt: int = Field(
        ...,
        ge=1,
        description="1-indexed attempt number for this stage execution. "
        "Distinguishes a first run from a local retry.",
    )
    input_hash: str = Field(
        ...,
        description="SHA-256 hex digest of this stage's input payload. "
        "Changes whenever the upstream input changes.",
        min_length=64,
        max_length=64,
    )
    artifact_refs: list[ArtifactRefV1] = Field(
        default_factory=list,
        description="References to artifacts produced/consumed by this "
        "stage. Never raw bytes.",
    )
    validation_ref: Optional[ArtifactRefV1] = Field(
        default=None,
        description="Reference to this stage's ValidationReportV1 artifact "
        "(e.g. a stored validation_report.json), not the report inline.",
    )
    provider: ProviderDescriptorV1 = Field(
        ...,
        description="Descriptor of whichever provider implementation "
        "executed this stage, keyed by capability rather than vendor name.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when this envelope was created.",
    )

    model_config = {
        "extra": "forbid",  # no free-form payloads sneaking in
    }


# Deliverable: StageOutputV1

class StageOutputV1(BaseModel):
    """
    Generic parent model for every stage's output.

    Deliberately generic today, per the runbook: this is NOT ScriptOutput,
    VoiceOutput, or AvatarOutput. Every later stage-specific output will
    extend this model rather than the pipeline inventing one-off shapes now.
    """

    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Stage-specific structured data. Kept generic (a dict) "
        "at this stage; concrete stage outputs will subclass this model "
        "later rather than redefining payload shape here.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Non-payload contextual data about this output, e.g. "
        "timing, cost, or provider-specific notes.",
    )
    artifact_refs: list[ArtifactRefV1] = Field(
        default_factory=list,
        description="References to any artifacts this stage produced.",
    )

    model_config = {
        "extra": "forbid",
    }


# Deliverable: ValidationReportV1

class ValidationReportV1(BaseModel):
    """
    Result of running a stage's validator(s) against its output.

    Minimum required per the runbook:
        - pass/fail
        - failure list

    failure_type is machine-readable, distinct from `failures` (which is
    the human-readable message list). A validator that fails should set
    both: `failures` for a person reading a log or dashboard, `failure_type`
    for code deciding what to do next (e.g. whether pipeline.py's
    correction loop should retry this stage locally).
    """

    passed: bool = Field(
        ..., description="Whether the stage's output passed validation."
    )
    failures: list[str] = Field(
        default_factory=list,
        description="Human-readable list of validation failures. Empty "
        "when passed=True. Examples: 'Voice duration mismatch', "
        "'Missing captions', 'Invalid hash'.",
    )
    stage_id: str = Field(
        ..., description="Stage/gate this validation report applies to."
    )
    failure_type: Optional[str] = Field(
        default=None,
        description="Machine-readable failure category set by the validator "
        "that produced this report, e.g. 'hash_mismatch', "
        "'duration_mismatch', 'missing_captions'. None when passed=True, "
        "or when a validator hasn't been updated to set one - callers "
        "should not assume a missing failure_type means 'hash_mismatch'.",
    )
    validated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when validation ran.",
    )

    model_config = {
        "extra": "forbid",
    }