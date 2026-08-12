"""
providers/stub_qc.py

S70 Quality Control Stub Provider.
"""

import json

from contracts.common.envelope import StageEnvelopeV1, StageOutputV1
from contracts.stages.s70_qc import QualityReportV1
from orchestrator.storage import put_artifact


class StubQCProvider:
    capability: str = "quality_control"

    def run(self, envelope: StageEnvelopeV1, run_id: str) -> StageOutputV1:
        video_ref = next(
            (
                ref
                for ref in envelope.artifact_refs
                if ref.artifact_id.startswith("master_video_")
            ),
            None,
        )
        if video_ref is None:
            raise ValueError(
                "S70 QC stub requires the S60 master_video_ artifact "
                "in envelope.artifact_refs"
            )

        qc_report = QualityReportV1(
            run_id=run_id,
            master_video_hash=video_ref.hash,
            passed=True,
            metrics={"identity_similarity": 0.96, "sync_score": 0.98},
        )

        report_bytes = json.dumps(qc_report.model_dump()).encode("utf-8")
        artifact = put_artifact(
            data=report_bytes,
            artifact_id=f"qc_report_{run_id}",
            mime_type="application/json",
        )

        return StageOutputV1(
            payload=qc_report.model_dump(),
            metadata={
                "stub": True,
                "provider": "stub_qc_provider",
                "passed": qc_report.passed,
            },
            artifact_refs=[artifact],
        )