"""
tests/test_fixtures.py

Validates every fixtures/valid/*.json against its model (should pass) and
every fixtures/malformed/*.json (should fail). Uses pytest.mark.parametrize
so each fixture is its own reported test case - a broken fixture fails the
build individually, rather than being silently swallowed inside a script
that only prints to stdout.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.registry.profiles import (
    StyleProfileV1,
    PolicyProfileV1,
    ConsentGrantV1,
    ProviderConfigV1,
)
from contracts.common.envelope import (
    ArtifactRefV1,
    ProviderDescriptorV1,
    StageEnvelopeV1,
    StageOutputV1,
    ValidationReportV1,
)
from contracts.common.manifest import ProductionManifestV1, StageRecordV1
from contracts.stages.g90_disclosure import DisclosureDecisionV1
from contracts.stages.s10_script import ScriptPackageV1
from contracts.stages.s40_sync import SynchronizedMediaV1
from contracts.stages.s70_qc import QualityReportV1
from contracts.stages.g80_approval import HumanApprovalV1
from contracts.stages.s100_publish import PublishReceiptV1
from contracts.stages.idea_request import IdeaRequestV1
from contracts.stages.s60_assembly import MasterVideoV1
from contracts.common.telemetry import StageRunRecordV1
from contracts.stages.s50_captions import CaptionTrackV1
from contracts.registry.identity_profile import IdentityProfileV1
from contracts.registry.voice_profile import VoiceProfileV1
from contracts.stages.s20_voice import VoiceTrackV1
from contracts.stages.s30_avatar import VisualRequestV1, PrimaryVisualTrackV1
from contracts.common.validation_failure import ValidationFailureV1
from contracts.common.correction_plan import CorrectionPlanV1

VALID_FIXTURES = [
    ("fixtures/valid/style_profile.json", StyleProfileV1),
    ("fixtures/valid/policy_profile.json", PolicyProfileV1),
    ("fixtures/valid/consent_grant.json", ConsentGrantV1),
    ("fixtures/valid/provider_config.json", ProviderConfigV1),
    ("fixtures/valid/disclosure_decision.json", DisclosureDecisionV1),
    ("fixtures/valid/script_package.json", ScriptPackageV1),
    ("fixtures/valid/synchronized_media.json", SynchronizedMediaV1),
    ("fixtures/valid/publish_receipt.json", PublishReceiptV1),
    ("fixtures/valid/artifact_ref.json", ArtifactRefV1),
    ("fixtures/valid/provider_descriptor.json", ProviderDescriptorV1),
    ("fixtures/valid/stage_envelope.json", StageEnvelopeV1),
    ("fixtures/valid/stage_output.json", StageOutputV1),
    ("fixtures/valid/validation_report.json", ValidationReportV1),
    ("fixtures/valid/stage_record.json", StageRecordV1),
    ("fixtures/valid/production_manifest.json", ProductionManifestV1),
    ("fixtures/valid/quality_report.json", QualityReportV1),
    ("fixtures/valid/human_approval.json", HumanApprovalV1),
    ("fixtures/valid/idea_request_avatar.json", IdeaRequestV1),
    ("fixtures/valid/idea_request_faceless.json",IdeaRequestV1),
    ("fixtures/valid/master_video.json", MasterVideoV1),
    ("fixtures/valid/caption_track.json", CaptionTrackV1),
    ("fixtures/valid/stage_run_record.json", StageRunRecordV1),
    ("fixtures/valid/identity_profile.json", IdentityProfileV1),
    ("fixtures/valid/voice_profile.json", VoiceProfileV1),
    ("fixtures/valid/voice_track.json", VoiceTrackV1),
    ("fixtures/valid/visual_request_avatar.json", VisualRequestV1),
    ("fixtures/valid/visual_request_faceless.json", VisualRequestV1),
    ("fixtures/valid/primary_visual_track.json", PrimaryVisualTrackV1),
    ("fixtures/valid/validation_failure.json", ValidationFailureV1),
    ("fixtures/valid/correction_plan.json", CorrectionPlanV1),
]

MALFORMED_FIXTURES = [
    ("fixtures/malformed/style_profile_missing_style_id.json", StyleProfileV1),
    ("fixtures/malformed/policy_profile_missing_applies_to.json", PolicyProfileV1),
    ("fixtures/malformed/consent_grant_missing_granted_by.json", ConsentGrantV1),
    ("fixtures/malformed/provider_config_missing_provider_name.json", ProviderConfigV1),
    ("fixtures/malformed/disclosure_decision_invalid_modality.json", DisclosureDecisionV1),
    ("fixtures/malformed/script_package_empty_scenes.json", ScriptPackageV1),
    ("fixtures/malformed/synchronized_media_missing_artifact.json", SynchronizedMediaV1),
    ("fixtures/malformed/publish_receipt_missing_privacy.json", PublishReceiptV1),
    ("fixtures/malformed/artifact_ref_short_hash.json", ArtifactRefV1),
    ("fixtures/malformed/provider_descriptor_missing_provider.json", ProviderDescriptorV1),
    ("fixtures/malformed/stage_envelope_missing_provider.json", StageEnvelopeV1),
    ("fixtures/malformed/stage_output_bad_metadata_type.json", StageOutputV1),
    ("fixtures/malformed/validation_report_missing_stage_id.json", ValidationReportV1),
    ("fixtures/malformed/stage_record_invalid_status.json", StageRecordV1),
    ("fixtures/malformed/production_manifest_missing_idea_request_id.json", ProductionManifestV1),
    ("fixtures/malformed/quality_report_short_hash.json", QualityReportV1),
    ("fixtures/malformed/human_approval_invalid_decision.json", HumanApprovalV1),
    ("fixtures/malformed/idea_request_avatar_missing_identity.json",IdeaRequestV1),
    ("fixtures/malformed/master_video_duration_zero.json", MasterVideoV1),
    ("fixtures/malformed/caption_track_negative_word_count.json", CaptionTrackV1),
    ("fixtures/malformed/stage_run_record_missing_provider.json", StageRunRecordV1),
    ("fixtures/malformed/identity_profile_bad_consent_type.json", IdentityProfileV1),
    ("fixtures/malformed/voice_profile_bad_consent_type.json", VoiceProfileV1),
    ("fixtures/malformed/voice_track_zero_duration.json", VoiceTrackV1),
    ("fixtures/malformed/visual_request_zero_scenes.json", VisualRequestV1),
    ("fixtures/malformed/primary_visual_track_missing_artifact.json", PrimaryVisualTrackV1),
    ("fixtures/malformed/validation_failure_missing_message.json", ValidationFailureV1),
    ("fixtures/malformed/correction_plan_negative_retry_budget.json", CorrectionPlanV1),
]


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


@pytest.mark.parametrize("path,model", VALID_FIXTURES, ids=[p for p, _ in VALID_FIXTURES])
def test_valid_fixture_is_accepted(path, model):
    data = _load(path)
    model.model_validate(data)  # raises ValidationError -> test fails, if bad


@pytest.mark.parametrize("path,model", MALFORMED_FIXTURES, ids=[p for p, _ in MALFORMED_FIXTURES])
def test_malformed_fixture_is_rejected(path, model):
    data = _load(path)
    with pytest.raises(ValidationError):
        model.model_validate(data)