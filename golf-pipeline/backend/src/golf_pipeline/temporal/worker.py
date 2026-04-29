"""Temporal worker entry point.

Run with:
    python -m golf_pipeline.temporal.worker
"""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from golf_pipeline.config import get_config
from golf_pipeline.db.client import ensure_indexes
from golf_pipeline.temporal.activities import ALL_ACTIVITIES
from golf_pipeline.temporal.workflows import ProcessSession, ProcessSwing


async def main():
    cfg = get_config()
    logging.basicConfig(level=cfg.log_level)

    await ensure_indexes()

    client = await Client.connect(cfg.temporal.target, namespace=cfg.temporal.namespace)
    worker = Worker(
        client,
        task_queue=cfg.temporal.task_queue,
        workflows=[ProcessSession, ProcessSwing],
        activities=ALL_ACTIVITIES,
    )
    print(f"[worker] connected to {cfg.temporal.target}, queue={cfg.temporal.task_queue}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
