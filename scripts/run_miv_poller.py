"""Poll the Flemish detector feed on a fixed interval, around the clock. Start with: python scripts/run_miv_poller.py

Collection never stops at night: the days x hours picture needs all 168 hours of the week.
Interval defaults to 5 minutes (TRAVELSMART_MIV_INTERVAL_MINUTES); the feed itself updates every minute.
"""
import logging
import os
import time

import httpx
from apscheduler.schedulers.blocking import BlockingScheduler
from travelsmart.main import Session, miv_collect

interval = max(1, int(os.getenv("TRAVELSMART_MIV_INTERVAL_MINUTES", "5")))
RETRY_WAITS = (15, 45)  # seconds before the 2nd and 3rd attempt when the network hiccups (DNS, connect, timeouts)

def poll():
    for attempt, wait in enumerate((0, *RETRY_WAITS), start=1):
        time.sleep(wait)
        try:
            with Session() as session:
                miv_collect(session)
            return
        except httpx.TransportError as error:  # transient network trouble: retry within this poll's slot
            logging.warning("miv poll attempt %s failed (network): %s", attempt, error)
        except Exception:
            logging.exception("miv poll failed")  # anything else (bad data, certificate failure) is not retried
            return
    logging.error("miv poll gave up after %s attempts; the next poll runs on schedule", len(RETRY_WAITS) + 1)

poll()  # first poll immediately
scheduler = BlockingScheduler(timezone="Europe/Brussels")
scheduler.add_job(poll, "interval", minutes=interval, max_instances=1, coalesce=True)
scheduler.start()
