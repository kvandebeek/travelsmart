"""Run one collection job on a fixed interval, around the clock, with short retries for network hiccups."""
from __future__ import annotations

import logging
import time

import httpx

RETRY_WAITS = (15, 45)  # seconds before the 2nd and 3rd attempt when the network hiccups (DNS, connect, timeouts)


def poll_with_retries(name: str, job) -> None:
    for attempt, wait in enumerate((0, *RETRY_WAITS), start=1):
        time.sleep(wait)
        try:
            job()
            return
        except httpx.TransportError as error:  # transient network trouble: retry within this poll's slot
            logging.warning("%s poll attempt %s failed (network): %s", name, attempt, error)
        except Exception:
            logging.exception("%s poll failed", name)  # anything else (bad data, certificate failure) is not retried
            return
    logging.error("%s poll gave up after %s attempts; the next poll runs on schedule", name, len(RETRY_WAITS) + 1)


def run_forever(name: str, job, interval_minutes: int) -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    poll = lambda: poll_with_retries(name, job)
    poll()  # first poll immediately
    scheduler = BlockingScheduler(timezone="Europe/Brussels")
    scheduler.add_job(poll, "interval", minutes=interval_minutes, max_instances=1, coalesce=True)
    scheduler.start()
