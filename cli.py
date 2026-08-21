from dotenv import load_dotenv
load_dotenv()

import argparse
import asyncio
import sys
import threading
import time
from pathlib import Path

import yaml
from temporalio.client import Client
from temporalio.worker import Worker

from datetime import datetime

from contracts.stages.idea_request import IdeaRequestV1, Modality
from orchestrator.manifest_store import load_manifest
from orchestrator.pipeline import TASK_QUEUE, AvatarPipeline
from orchestrator.registry import register_all_stubs, try_register_real_providers
from orchestrator.activities import run_stage, record_g80_approval, run_intake_stage,fetch_identity_reference
from orchestrator.consent_gate import validate_run
from orchestrator.manifest_store import get_connection
from contracts.stages.g80_approval import ApprovalDecision, HumanApprovalV1
from datetime import datetime, timezone
from orchestrator.manifest_store import get_connection


TEMPORAL_HOST = "localhost:7233"


# ── Worker (runs in background thread) ────────────────────────────────────────

def _run_worker():
    """Start the Temporal worker in a daemon thread."""

    async def _worker_main():
        register_all_stubs()
        try_register_real_providers()
        client = await Client.connect(TEMPORAL_HOST)
        worker = Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[AvatarPipeline],
            activities=[run_stage, record_g80_approval, run_intake_stage,fetch_identity_reference],
        )
        print("[worker] Started on task queue: avatar-harness")
        await worker.run()

    asyncio.run(_worker_main())


# ── Pipeline trigger ──────────────────────────────────────────────────────────

async def _start_pipeline(idea: IdeaRequestV1) -> tuple[Client,str]:
    client = await Client.connect(TEMPORAL_HOST)
    handle = await client.start_workflow(
        AvatarPipeline.run,
        idea.model_dump_json(),
        id=f"pipeline-{idea.idea_request_id}",
        task_queue=TASK_QUEUE,
    )
    return client, handle.id


# ── Wait for G80 ──────────────────────────────────────────────────────────────

async def _wait_for_g80(run_id: str, timeout: int = 680) -> str:
    """Poll manifest until S00-S70 all passed, meaning pipeline is at G80."""
   

    print("[pipeline] Waiting for stages S00→S70 to complete...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM manifest_stage_records "
                        "WHERE run_id = %s AND status = 'passed'",
                        (run_id,),
                    )
                    row = cur.fetchone()
                    count = row[0] if row else 0
                    if count >= 8:  # S00 through S70
                        print(f"[pipeline] {count} stages passed — pipeline paused at G80 ✓")
                        return run_id
        except Exception:
            pass
        await asyncio.sleep(2)
    raise TimeoutError("Timed out waiting for G80 pause")


# ── Approve ───────────────────────────────────────────────────────────────────
# for real pipeline we would probably need to pass the real hash and fail 
# if its not present -- note
async def _approve(client:Client,workflow_id: str, run_id: str):


    master_hash = None
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT output_hash FROM stage_run_records WHERE run_id = %s AND stage_id = 'S60'",
                    (run_id,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    master_hash = row[0]
    except Exception as e:
        print(f"[approve] Warning: could not fetch S60 hash: {e}")

    if not master_hash:
        master_hash = "0" * 64
        print("[approve] Warning: using fallback hash")

    print(f"[approve] Signing master video hash: {master_hash[:16]}...")

    approval = HumanApprovalV1(
        reviewer_id="cli_auto_reviewer",
        decision=ApprovalDecision.APPROVED,
        master_video_hash=master_hash,
        timestamp=datetime.now(timezone.utc),
        comments="Auto-approved via avatar-harness CLI",
    )
    handle = client.get_workflow_handle(workflow_id)
    await handle.signal("approve", approval.model_dump(mode="json"))
    print("[approve] HumanApprovalV1 signal sent ✓")


# ── Wait for completion ────────────────────────────────────────────────────────

async def _wait_for_completion(client:Client,workflow_id: str, timeout: int = 60) -> dict:
    """
    Wait for the workflow to finish and return its final result dict.

    Previously this discarded handle.result() entirely, which is exactly
    why _verify() had no way to check real G90/S100 content and fell back
    to presence-only checks (Ammar Day 2 Part 2). Capturing it here — and
    AvatarPipeline.run() returning both G90 and S100's payloads, not just
    S100's — is what makes the real content check possible.
    """
    handle = client.get_workflow_handle(workflow_id)
    print("[pipeline] Waiting for G90 + S100 to complete...")
    result = await asyncio.wait_for(handle.result(), timeout=timeout)
    print("[pipeline] Workflow completed ✓")
    return result


# ── Verify ────────────────────────────────────────────────────────────────────

def _verify(run_id: str, privacy: str, workflow_result: dict | None = None):
    print("\n" + "=" * 60)
    print("M1 VERIFICATION")
    print("=" * 60)

    try:
        manifest = load_manifest(run_id)
    except Exception as e:
        print(f"✗ Could not load manifest: {e}")
        sys.exit(1)

    total_stages = len(manifest.stages)

    passed = 0
    sha_valid = True

    for stage in manifest.stages:
        status = stage.status.value
        mark = "✓" if status == "passed" else "✗"
        print(f"  {mark} {stage.stage_id:6} | {status:8} | {len(stage.output_artifact_ids)} artifact(s)")
        if status == "passed":
            passed += 1


    checkpoint_count = passed
    passed_stage_ids = {s.stage_id for s in manifest.stages if s.status.value == "passed"}
    total_stages = len(set(s.stage_id for s in manifest.stages))
    checkpoint_count = len(passed_stage_ids)

    # ── Real content checks (Part 2) ────────────────────────────────────────
    synthetic_flag = False
    receipt_found = False
    receipt_privacy = None
    receipt_dry_run = None

    if workflow_result:
        g90_payload = (workflow_result.get("G90") or {}).get("payload") or {}
        s100_payload = (workflow_result.get("S100") or {}).get("payload") or {}

        synthetic_flag = bool(g90_payload.get("contains_synthetic_media", False))
        receipt_found = bool(s100_payload)
        receipt_privacy = s100_payload.get("privacy")
        receipt_dry_run = s100_payload.get("dry_run")
    else:
        # Fallback to presence-only check if no result was captured
        # (e.g. verify run against a past run_id with no live workflow handle).
        for stage in manifest.stages:
            if stage.stage_id == "G90" and stage.status.value == "passed":
                synthetic_flag = True
            if stage.stage_id == "S100" and stage.status.value == "passed":
                receipt_found = True

    effective_privacy = receipt_privacy or privacy

    print()
    print(f"  Stages passed:      {passed}/{total_stages}")
    print(f"  checkpoint_count:   {checkpoint_count}/{total_stages}")
    print(f"  SHA checks:         {'✓ verified by stage_executor' if passed == total_stages else '✗ not all passed'}")
    print(f"  containsSyntheticMedia=true: {'✓' if synthetic_flag else '✗ G90 did not confirm true'}")
    print(f"  privacy=unlisted:   {'✓' if effective_privacy == 'unlisted' else f'✗ was {effective_privacy}'}")
    print(f"  publish receipt:    {'✓' if receipt_found else '✗ S100 missing'}")
    if receipt_dry_run is not None:
        print(f"  dry_run:            {receipt_dry_run}")

    print()
    all_pass = (
        checkpoint_count >= total_stages
        and sha_valid
        and synthetic_flag
        and receipt_found
        and effective_privacy == "unlisted"
    )

    if all_pass:
        print("M1 RESULT: ✓ PASS")
    else:
        print("M1 RESULT: ✗ FAIL — see above")
        sys.exit(1)


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(prog="avatar-harness")
    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="Run the full pipeline end to end")
    run_parser.add_argument("--run-id", default=None, help="Override run ID (default: auto-generated timestamp)")
    run_parser.add_argument("--config", required=True, help="Path to run config YAML")
    run_parser.add_argument("--idea", required=False, help="Override topic from config")
    run_parser.add_argument("--privacy", default="unlisted", choices=["unlisted", "private"])

    args = parser.parse_args()

    if args.command != "run":
        parser.print_help()
        sys.exit(1)

    # Load config
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config not found: {config_path}")
        sys.exit(1)

    with open(config_path) as f:
        config = yaml.safe_load(f)

    topic = args.idea or config.get("topic", "M1 walking skeleton")
    run_id = args.run_id or config.get("run_id") or f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    modality = Modality(config.get("modality", "AVATAR"))
    identity_id = config.get("identity_id") if modality == Modality.AVATAR else None

    idea = IdeaRequestV1(
        idea_request_id=run_id,
        modality=modality,
        topic=topic,
        identity_id=identity_id,
        voice_id=config.get("voice_id", "voice_001"),
        style_id=config.get("style_id"),
    )
    validate_run(idea)

    print(f"\n{'='*60}")
    print(f"AVATAR HARNESS — run")
    print(f"  run_id:   {run_id}")
    print(f"  topic:    {topic}")
    print(f"  modality: {modality.value}")
    print(f"  privacy:  {args.privacy}")
    print(f"{'='*60}\n")

  
    worker_thread = threading.Thread(target=_run_worker, daemon=True)
    worker_thread.start()
    time.sleep(2) 

    client,workflow_id = asyncio.run(_start_pipeline(idea))
    print(f"[pipeline] Started workflow: {workflow_id}")
    print(f"[pipeline] Monitor: http://localhost:8080/namespaces/default/workflows/{workflow_id}")

    asyncio.run(_wait_for_g80(run_id))

    asyncio.run(_approve(client,workflow_id, run_id))


    workflow_result = asyncio.run(_wait_for_completion(client,workflow_id))

    
    _verify(run_id, args.privacy, workflow_result)


if __name__ == "__main__":
    main()