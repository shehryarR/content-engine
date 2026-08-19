import asyncio
import json
import subprocess
import time

import pytest

from contracts.common.envelope import StageEnvelopeV1, StageOutputV1
from contracts.common.manifest import StageStatus
from contracts.stages.idea_request import IdeaRequestV1, Modality
from contracts.stages.s20_voice import VoiceTrackV1
from orchestrator import registry
from orchestrator.activities import record_g80_approval, run_intake_stage, run_stage
from orchestrator.manifest_store import get_connection, load_manifest
from orchestrator.pipeline import AvatarPipeline, TASK_QUEUE
from orchestrator.storage import put_artifact
from temporalio import activity
from temporalio.exceptions import FailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from datetime import datetime, timezone


def _make_valid_wav_bytes() -> bytes:
    """Generate a valid 3-second WAV using ffmpeg's built-in sine generator.
    Guaranteed to pass ffprobe on any platform since ffmpeg produced it.
    ffmpeg is available on CI (it's already used by the voice and assembly validators).
    """
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-ar", "22050", "-ac", "1",
            "-f", "wav", "pipe:1",
        ],
        capture_output=True,
        timeout=15,
    )
    if not proc.stdout:
        raise RuntimeError(f"ffmpeg WAV generation failed: {proc.stderr[:300]}")
    return proc.stdout


@activity.defn(name="fetch_identity_reference")
async def mock_fetch_identity_reference(identity_id: str) -> dict:
    """Lightweight stand-in — avoids a real DB lookup during tests."""
    data = json.dumps({"stub": True}).encode()
    ref = put_artifact(data=data, artifact_id=f"identity_ref_{identity_id}", mime_type="image/png")
    return ref.model_dump()


class _FailOnceThenSucceed:
    """
    Wraps a capability: returns a bad fixture on attempt 1,
    then a valid payload on every subsequent call.
    """

    def __init__(self, capability: str, bad_fixture_path: str, good_payload: dict):
        self._capability = capability
        self._bad_fixture_path = bad_fixture_path
        self._good_payload = good_payload
        self._calls = 0

    @property
    def capability(self) -> str:
        return self._capability

    def run(self, envelope: StageEnvelopeV1, run_id: str, *args, **kwargs) -> StageOutputV1:
        self._calls += 1

        if self._calls == 1:
            with open(self._bad_fixture_path, "rb") as f:
                bad_bytes = f.read()
            artifact = put_artifact(
                data=bad_bytes,
                artifact_id=f"{self._capability}_{run_id}_bad_attempt1",
                mime_type="application/json",
            )
            return StageOutputV1(
                payload=json.loads(bad_bytes),
                metadata={"provider": "mock_fail"},
                artifact_refs=[artifact],
            )

        good_bytes = json.dumps(self._good_payload).encode()
        artifact = put_artifact(
            data=good_bytes,
            artifact_id=f"{self._capability}_{run_id}_good",
            mime_type="application/json",
        )
        return StageOutputV1(
            payload=self._good_payload,
            metadata={"provider": "mock_success"},
            artifact_refs=[artifact],
        )


class _AlwaysFail:
    def __init__(self, capability: str, bad_fixture_path: str):
        self._capability = capability
        self._bad_fixture_path = bad_fixture_path

    @property
    def capability(self) -> str:
        return self._capability

    def run(self, envelope: StageEnvelopeV1, run_id: str, *args, **kwargs) -> StageOutputV1:
        with open(self._bad_fixture_path, "rb") as f:
            bad_bytes = f.read()
        artifact = put_artifact(
            data=bad_bytes,
            artifact_id=f"{self._capability}_{run_id}_bad_attempt{envelope.attempt}",
            mime_type="application/json",
        )
        return StageOutputV1(
            payload=json.loads(bad_bytes),
            metadata={"provider": "always_fail"},
            artifact_refs=[artifact],
        )


class _FailOnceVoiceThenSucceed:
    """
    Voice provider mock: returns corrupt_audio on attempt 1 (triggers voice_invalid),
    then generates a valid WAV in-memory on attempt 2 (passes validate_voice).
    WAV is generated via Python's wave module so it passes ffprobe on any platform.
    """

    def __init__(self, bad_fixture_path: str):
        self._capability = "voice_synthesis"
        self._bad_fixture_path = bad_fixture_path
        self._calls = 0

    @property
    def capability(self) -> str:
        return self._capability

    def run(self, envelope: StageEnvelopeV1, run_id: str, *args, **kwargs) -> StageOutputV1:
        self._calls += 1

        if self._calls == 1:
            with open(self._bad_fixture_path, "rb") as f:
                audio_data = f.read()
            mime = "audio/wav"
        else:
            # Generate a valid 3s sine wave WAV in-memory — no file dependency.
            audio_data = _make_valid_wav_bytes(duration_s=3.0)
            mime = "audio/wav"

        artifact_id = f"voice_{envelope.stage_id}_{envelope.attempt}_{datetime.now(timezone.utc).isoformat()}"
        artifact_ref = put_artifact(
            data=audio_data,
            artifact_id=artifact_id,
            mime_type=mime,
        )

        voice_track = VoiceTrackV1(
            run_id=run_id,
            voice_id="stub_voice_001",
            audio_artifact=artifact_ref,
            duration_seconds=3.0,
        )

        return StageOutputV1(
            payload=voice_track.model_dump(),
            metadata={"provider": "mock_voice"},
            artifact_refs=[artifact_ref],
        )


@pytest.fixture(autouse=True)
def _clean_db():
    """Wipe test rows before each test so runs are isolated."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM manifest_stage_records WHERE run_id LIKE 'test_inj_%'")
        conn.commit()


def test_s10_failure_injection():
    asyncio.run(_run())


async def _run():
    run_id = "test_inj_s10"

    # Register stubs first, then overwrite script_generation with our mock.
    # registry.register() mutates _providers in-place, so get_provider()
    # inside stage_executor picks it up without any module-level patching.
    registry.register_all_stubs()
    mock_s10 = _FailOnceThenSucceed(
        capability="script_generation",
        bad_fixture_path="fixtures/failures/malformed_script.json",
        good_payload={"run_id": run_id, "scenes": ["Scene 1 text", "Scene 2 text", "Scene 3 text"]},
    )
    registry.register(mock_s10)  # overwrites StubScriptProvider

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[AvatarPipeline],
            activities=[run_stage, record_g80_approval, run_intake_stage, mock_fetch_identity_reference],
        ):
            idea = IdeaRequestV1(
                idea_request_id=run_id,
                modality=Modality.AVATAR,
                topic="Failure Injection Test",
                identity_id="identity_001",
                voice_id="voice_001",
            )

            await env.client.start_workflow(
                AvatarPipeline.run,
                args=[idea.model_dump_json()],
                id=f"wf_{run_id}",
                task_queue=TASK_QUEUE,
            )

            # Poll until S10 attempt 2 appears as PASSED (or timeout).
            deadline = time.time() + 15
            while time.time() < deadline:
                try:
                    stages = load_manifest(run_id).stages
                    s10 = [s for s in stages if s.stage_id == "S10"]
                    if any(s.attempt == 2 and s.status == StageStatus.PASSED for s in s10):
                        break
                except ValueError:
                    pass
                await asyncio.sleep(0.2)
            else:
                # Print what we have before failing so it's easy to diagnose.
                try:
                    for s in load_manifest(run_id).stages:
                        print(f"  {s.stage_id} attempt={s.attempt} {s.status}")
                except ValueError:
                    print("  (no manifest rows at all)")
                pytest.fail("Timed out waiting for S10 attempt 2 to pass")

            stages = load_manifest(run_id).stages

            # S00 ran exactly once.
            s00 = [s for s in stages if s.stage_id == "S00"]
            assert len(s00) == 1
            assert s00[0].status == StageStatus.PASSED

            # S10 ran twice: attempt 1 FAILED, attempt 2 PASSED.
            s10 = [s for s in stages if s.stage_id == "S10"]
            assert len(s10) == 2, f"Expected 2 S10 rows, got {len(s10)}: {[(s.attempt, s.status) for s in s10]}"
            assert next(s for s in s10 if s.attempt == 1).status == StageStatus.FAILED
            assert next(s for s in s10 if s.attempt == 2).status == StageStatus.PASSED


def test_s10_retry_budget_exhaustion():
    asyncio.run(_run_budget_exhaustion())


async def _run_budget_exhaustion():
    run_id = "test_inj_s10_exhaust"

    registry.register_all_stubs()
    mock_s10 = _AlwaysFail(
        capability="script_generation",
        bad_fixture_path="fixtures/failures/persistently_malformed_script.json",
    )
    registry.register(mock_s10)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[AvatarPipeline],
            activities=[run_stage, record_g80_approval, run_intake_stage, mock_fetch_identity_reference],
        ):
            idea = IdeaRequestV1(
                idea_request_id=run_id,
                modality=Modality.AVATAR,
                topic="Retry Budget Test",
                identity_id="identity_001",
                voice_id="voice_001",
            )

            # Expected to fail due to ApplicationError at workflow level when retries exhausted
            with pytest.raises(Exception):
                await env.client.execute_workflow(
                    AvatarPipeline.run,
                    args=[idea.model_dump_json()],
                    id=f"wf_{run_id}",
                    task_queue=TASK_QUEUE,
                )

            # Verify manifest has 3 FAILED attempts for S10 (default retry_budget is 3 in pipeline config)
            stages = load_manifest(run_id).stages
            s10 = [s for s in stages if s.stage_id == "S10"]
            assert len(s10) == 3, f"Expected 3 S10 failure rows, got {len(s10)}"
            for s in s10:
                assert s.status == StageStatus.FAILED


def test_s20_failure_injection():
    asyncio.run(_run_s20())


async def _run_s20():
    run_id = "test_inj_s20"

    registry.register_all_stubs()
    mock_s20 = _FailOnceVoiceThenSucceed(
        bad_fixture_path="fixtures/failures/corrupt_audio.wav",
    )
    registry.register(mock_s20)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[AvatarPipeline],
            activities=[run_stage, record_g80_approval, run_intake_stage, mock_fetch_identity_reference],
        ):
            idea = IdeaRequestV1(
                idea_request_id=run_id,
                modality=Modality.AVATAR,
                topic="Failure Injection Test S20",
                identity_id="identity_001",
                voice_id="voice_001",
            )

            # The workflow will proceed past S20 and eventually hit the G80 human-approval
            # gate, which times out in the time-skipping environment (no signal sent).
            # We catch that expected failure and then assert on the manifest directly.
            try:
                await env.client.execute_workflow(
                    AvatarPipeline.run,
                    args=[idea.model_dump_json()],
                    id=f"wf_{run_id}",
                    task_queue=TASK_QUEUE,
                )
            except (FailureError, Exception):
                pass  # G80 timeout is expected; we only care about S20 manifest entries.

            stages = load_manifest(run_id).stages

            # S00 and S10 ran exactly once — upstream was untouched.
            s00 = [s for s in stages if s.stage_id == "S00"]
            assert len(s00) == 1, f"Expected 1 S00 row, got {len(s00)}"
            assert s00[0].status == StageStatus.PASSED

            s10 = [s for s in stages if s.stage_id == "S10"]
            assert len(s10) == 1, f"Expected 1 S10 row, got {len(s10)}"
            assert s10[0].status == StageStatus.PASSED

            # S20 ran twice: attempt 1 FAILED (corrupt audio), attempt 2 PASSED.
            s20 = [s for s in stages if s.stage_id == "S20"]
            assert len(s20) == 2, f"Expected 2 S20 rows, got {len(s20)}: {[(s.attempt, s.status) for s in s20]}"
            assert next(s for s in s20 if s.attempt == 1).status == StageStatus.FAILED
            assert next(s for s in s20 if s.attempt == 2).status == StageStatus.PASSED
