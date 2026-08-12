"""
orchestrator/worker.py

The worker process. Registers both the hello-world workflow (stack health check)
and the real AvatarPipeline workflow.
"""
from dotenv import load_dotenv
load_dotenv()
import asyncio

from temporalio.client import Client
from temporalio.worker import Worker




from orchestrator.pipeline import AvatarPipeline
from orchestrator.activities import run_stage


from orchestrator.registry import register

from orchestrator.activities import run_stage, record_g80_approval,run_intake_stage
from orchestrator.pipeline import AvatarPipeline, TASK_QUEUE
from orchestrator.registry import register_all_stubs, try_register_real_providers


import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

TEMPORAL_HOST = "localhost:7233"

async def main():
    
    register_all_stubs()
    try_register_real_providers()
    
    client = await Client.connect("localhost:7233")
    
    
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[AvatarPipeline],
        activities=[run_stage, record_g80_approval,run_intake_stage],
    )
    
    print(f"Starting worker on task queue: {TASK_QUEUE}")
    
    # Run the worker
    await worker.run()



if __name__ == "__main__":
    asyncio.run(main())