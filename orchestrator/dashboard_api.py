"""
orchestrator/dashboard_api.py

M6: FastAPI dashboard backend. Lets someone submit a run (topic, modality,
voice, identity) and watch per-stage telemetry live, without touching
contracts/ or graph/ - a UI layer over the existing pipeline, not a new
orchestration mechanism.

Reuses cli.py's own _run_worker/_start_pipeline/_wait_for_g80/_approve/
_wait_for_completion rather than reimplementing them - cli.py stays the
source of truth for "how a run actually gets kicked off"; this just does
it in a background thread instead of blocking a terminal. Fragility
tradeoff: if cli.py's private helper signatures change, this breaks too.

Faceless provider override: modality=faceless MUST explicitly pass
avatar_render=faceless_mixed_media + media_sync=narration_conform to the
worker. Leaving providers={} for a faceless run would let registry.py's
key-presence auto-detection silently resolve avatar/D-ID providers instead
- the exact "quiet fallback" class of bug M4's audit existed to catch.
"""
from dotenv import load_dotenv
load_dotenv()

import asyncio
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from contracts.stages.idea_request import IdeaRequestV1, Modality
from orchestrator.consent_gate import validate_run
from orchestrator.manifest_store import get_connection, load_manifest
from cli import _run_worker, _start_pipeline, _wait_for_g80, _approve, _wait_for_completion

app = FastAPI(title="Avatar Harness Dashboard")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent.parent / "dashboards"

# In-memory, process-lifetime run registry - same scope/limitation as
# stage_executor.py's _stage_result_cache. A dashboard restart loses
# in-flight status tracking, but not the underlying Postgres telemetry,
# which survives and is what get_run_status reads as the source of truth.
_runs: dict[str, dict] = {}

FACELESS_PROVIDERS = {
    "avatar_render": "faceless_mixed_media",
    "media_sync": "narration_conform",
}


class RunRequest(BaseModel):
    topic: str
    modality: str  # "avatar" | "faceless"
    voice_id: str
    identity_id: Optional[str] = None
    style_id: Optional[str] = None


@app.get("/")
def serve_dashboard():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/profiles")
def get_profiles():
    """Identity + voice choices for the dashboard's dropdowns, active
    consent only - same filter the registry itself should be respecting."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT identity_id, display_name FROM identity_profiles "
                "WHERE consent_status = 'active' ORDER BY display_name"
            )
            identities = [{"id": r[0], "name": r[1]} for r in cur.fetchall()]

            cur.execute(
                "SELECT voice_id, display_name FROM voice_profiles "
                "WHERE consent_status = 'active' ORDER BY display_name"
            )
            voices = [{"id": r[0], "name": r[1]} for r in cur.fetchall()]

    return {"identities": identities, "voices": voices}


def _background_run(run_id: str, idea: IdeaRequestV1, providers: dict):
    """Mirrors cli.py's main() sequence exactly, but non-blocking and
    tracked in _runs instead of printed to a terminal."""

    async def _drive():
        worker_thread = threading.Thread(
            target=_run_worker, args=(providers,), daemon=True
        )
        worker_thread.start()
        await asyncio.sleep(2)

        client, workflow_id = await _start_pipeline(idea)
        _runs[run_id]["workflow_id"] = workflow_id
        _runs[run_id]["status"] = "running_S00_S70"

        try:
            await _wait_for_g80(run_id)
        except TimeoutError:
            # _wait_for_g80 polls Postgres for passed-stage count but has
            # no way to see that the workflow itself already died (e.g.
            # a non-retryable ValidationFailure). Without this, the
            # dashboard would sit at "running" for up to 680s after the
            # pipeline already failed. Check the manifest directly for a
            # failed stage before re-raising as a real timeout.
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT stage_id, attempt FROM manifest_stage_records "
                        "WHERE run_id = %s AND status = 'failed' "
                        "ORDER BY attempt DESC LIMIT 1",
                        (run_id,),
                    )
                    row = cur.fetchone()
            if row:
                raise RuntimeError(f"Stage {row[0]} failed (attempt {row[1]}) — pipeline did not reach G80")
            raise
        _runs[run_id]["status"] = "awaiting_approval"

        await _approve(client, workflow_id, run_id)
        _runs[run_id]["status"] = "running_G90_S100"

        result = await _wait_for_completion(client, workflow_id)
        _runs[run_id]["status"] = "completed"
        _runs[run_id]["result"] = result

    try:
        asyncio.run(_drive())
    except Exception as e:
        _runs[run_id]["status"] = "failed"
        _runs[run_id]["error"] = str(e)


@app.post("/api/runs")
def create_run(req: RunRequest):
    run_id = f"run_dashboard_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    try:
        modality = Modality(req.modality)
    except ValueError:
        raise HTTPException(400, f"modality must be 'avatar' or 'faceless', got {req.modality!r}")

    idea = IdeaRequestV1(
        idea_request_id=run_id,
        modality=modality,
        topic=req.topic,
        identity_id=req.identity_id if modality == Modality.AVATAR else None,
        voice_id=req.voice_id,
        style_id=req.style_id,
    )

    try:
        validate_run(idea)
    except Exception as e:
        raise HTTPException(400, f"consent/validation failed: {e}")

    providers = FACELESS_PROVIDERS if modality == Modality.FACELESS else {}

    _runs[run_id] = {"status": "starting", "idea": idea.model_dump(mode="json")}

    thread = threading.Thread(target=_background_run, args=(run_id, idea, providers), daemon=True)
    thread.start()

    return {"run_id": run_id, "status": "starting"}


@app.get("/api/runs/{run_id}")
def get_run_status(run_id: str):
    """Merges in-memory high-level state (starting/awaiting_approval/
    completed/failed - not persisted anywhere else) with real
    stage_run_records telemetry from Postgres, which is the durable
    source of truth for what actually happened."""
    tracked = _runs.get(run_id)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT stage_id, attempt, provider_name, provider_model,
                       provider_cost, provider_latency_ms, started_at, ended_at,
                       input_hash, output_hash
                FROM stage_run_records
                WHERE run_id = %s
                ORDER BY started_at
                """,
                (run_id,),
            )
            rows = cur.fetchall()

    if not rows and tracked is None:
        raise HTTPException(404, f"No run found for run_id {run_id!r}")

    stages = [
        {
            "stage_id": r[0], "attempt": r[1], "provider": r[2], "model": r[3],
            "cost": r[4], "latency_ms": r[5],
            "started_at": r[6].isoformat() if r[6] else None,
            "ended_at": r[7].isoformat() if r[7] else None,
            "input_hash": r[8], "output_hash": r[9],
        }
        for r in rows
    ]

    manifest_status = None
    try:
        manifest = load_manifest(run_id)
        manifest_status = {
            "total_stages": len(manifest.stages),
            "passed": len([s for s in manifest.stages if s.status.value == "passed"]),
        }
    except Exception:
        pass

    return {
        "run_id": run_id,
        "high_level_status": (tracked or {}).get("status", "unknown (dashboard restarted mid-run)"),
        "idea": (tracked or {}).get("idea"),
        "manifest": manifest_status,
        "stages": stages,
        "error": (tracked or {}).get("error"),
    }
