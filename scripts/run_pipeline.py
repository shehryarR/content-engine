"""
scripts/run_pipeline.py

Triggers one AvatarPipeline run. The pipeline will pause at G80
waiting for an approval signal - use scripts/approve.py to unblock it.
"""

import asyncio
import json

from temporalio.client import Client

from contracts.stages.idea_request import IdeaRequestV1, Modality
from orchestrator.pipeline import TASK_QUEUE, AvatarPipeline

TEMPORAL_HOST = "localhost:7233"


async def main() -> None:
    idea = IdeaRequestV1(
        idea_request_id="fatima4",
        modality=Modality.FACELESS,
        topic="M1 walking skeleton test",
        voice_id="voice_001",
    )
    idea_json = idea.model_dump_json()

    client = await Client.connect(TEMPORAL_HOST)
    handle = await client.start_workflow(
        AvatarPipeline.run,
        idea_json,
        id=f"pipeline-{idea.idea_request_id}",
        task_queue=TASK_QUEUE,
    )
    print(f"Pipeline started: workflow_id={handle.id}")
    print(f"Run ID: {idea.idea_request_id}")
    print(f"Monitor at: http://localhost:8080/namespaces/default/workflows/{handle.id}")
    print(f"Send approval with: uv run python scripts/approve.py {handle.id} {idea.idea_request_id}")
    print(f"Verify manifest with: uv run python scripts/verify_manifest.py {idea.idea_request_id}")


if __name__ == "__main__":
    asyncio.run(main())