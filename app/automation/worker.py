"""Background worker: delivers queued customer messages and runs scheduled jobs.

    python -m app.automation.worker            # run forever
    python -m app.automation.worker --once     # one cycle (cron, debugging)

Run exactly one worker per database for the scheduled jobs; message delivery is safe with
more (rows are locked with SKIP LOCKED on PostgreSQL).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from datetime import UTC, datetime, timedelta

import httpx

from app.automation import inbound
from app.automation.jobs import run_daily_jobs
from app.automation.service import dispatch_outbox
from app.core.database import get_session

log = logging.getLogger("bud.worker")
DISPATCH_EVERY = 30  # seconds
JOBS_EVERY = timedelta(hours=1)
MAILBOX_EVERY = timedelta(minutes=2)


async def poll_mailbox() -> None:
    raw = await asyncio.to_thread(inbound.fetch_unseen)
    if not raw:
        return
    async with get_session() as session:
        counts = await inbound.process_mailbox(session, raw)
    log.info("mailbox: %s", counts)


async def cycle(client: httpx.AsyncClient, run_jobs: bool, poll: bool = False) -> None:
    if poll:
        await poll_mailbox()
    if run_jobs:
        async with get_session() as session:
            summary = await run_daily_jobs(session)
        log.info("jobs: %s", summary)
    async with get_session() as session:
        counts = await dispatch_outbox(session, client)
    if any(counts.values()):
        log.info("outbox: %s", counts)


async def main(once: bool) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    last_jobs: datetime | None = None
    last_poll: datetime | None = None
    async with httpx.AsyncClient() as client:
        while not stop.is_set():
            now = datetime.now(UTC)
            run_jobs = last_jobs is None or now - last_jobs >= JOBS_EVERY
            poll = last_poll is None or now - last_poll >= MAILBOX_EVERY
            try:
                await cycle(client, run_jobs, poll)
                if run_jobs:
                    last_jobs = now
                if poll:
                    last_poll = now
            except Exception:  # keep the worker alive; the next cycle retries
                log.exception("worker cycle failed")
            if once:
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=DISPATCH_EVERY)
            except TimeoutError:
                pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m app.automation.worker")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(main(args.once))
