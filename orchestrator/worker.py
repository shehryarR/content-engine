"""
orchestrator/worker.py

The worker process. Registers stub and (where configured) real providers,
then runs the AvatarPipeline workflow.
"""
from dotenv import load_dotenv
load_dotenv()
import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from orchestrator.activities import run_stage, record_g80_approval,run_intake_stage,fetch_identity_reference
from orchestrator.pipeline import AvatarPipeline, TASK_QUEUE
from orchestrator.registry import register_from_run_config


import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

TEMPORAL_HOST = "localhost:7233"

def _providers_from_env() -> dict[str, str]:
    """Standalone workers have no --config flag, so they read the same run
    config via RUN_CONFIG. Without this, a faceless run started against a
    standalone worker would silently resolve avatar providers and still
    produce a green M4 diff -- a passed milestone proving nothing."""
    import os
    from pathlib import Path
    import yaml

    path = os.environ.get("RUN_CONFIG")
    if not path:
        return {}
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise SystemExit(f"[worker] RUN_CONFIG points at a missing file: {path}")
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    return cfg.get("providers") or {}


async def main():

    resolved = register_from_run_config(_providers_from_env())
    print(f"[worker] provider resolution: {resolved}")
    
    client = await Client.connect("localhost:7233")
    
    
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[AvatarPipeline],
        activities=[run_stage, record_g80_approval,run_intake_stage,fetch_identity_reference],
    )
    
    print(f"Starting worker on task queue: {TASK_QUEUE}")
    
    # Run the worker
    await worker.run()



if __name__ == "__main__":
    asyncio.run(main())