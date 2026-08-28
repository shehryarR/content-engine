"""
orchestrator/publish_precondition.py

M5 step 6: named, testable publish precondition evaluator.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from contracts.stages.g80_approval import ApprovalDecision, HumanApprovalV1
from contracts.stages.g90_disclosure import DisclosureDecisionV1


@dataclass
class PreconditionResult:
    passed: bool
    failures: list[str] = field(default_factory=list)


def evaluate_publish_preconditions(
    approval: HumanApprovalV1 | dict | None,
    disclosure: DisclosureDecisionV1 | dict | None,
    current_master_hash: str | None,
) -> PreconditionResult:
    failures: list[str] = []

    if approval is None:
        failures.append("no approval decision exists — G80 not completed")
    else:
        if isinstance(approval, dict):
            decision = approval.get("decision", "")
            approval_hash = approval.get("master_video_hash", "")
        else:
            decision = (
                approval.decision.value
                if isinstance(approval.decision, ApprovalDecision)
                else str(approval.decision)
            )
            approval_hash = approval.master_video_hash

        if decision != ApprovalDecision.APPROVED.value and decision != "approved":
            failures.append(
                f"approval decision is '{decision}', not 'approved' "
                f"— publish must not proceed"
            )

        if current_master_hash and approval_hash != current_master_hash:
            failures.append(
                f"approval is for hash {approval_hash[:16]}..., "
                f"current master hash is {current_master_hash[:16]}... "
                f"— stale approval"
            )

    if disclosure is None:
        failures.append("no disclosure decision exists — G90 not completed")
    else:
        if isinstance(disclosure, dict):
            contains_synthetic = disclosure.get("contains_synthetic_media")
        else:
            contains_synthetic = disclosure.contains_synthetic_media

        if contains_synthetic is not True:
            failures.append(
                f"contains_synthetic_media is {contains_synthetic!r}, "
                f"must be True — publish must not proceed"
            )

    return PreconditionResult(passed=len(failures) == 0, failures=failures)