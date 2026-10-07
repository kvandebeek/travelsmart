"""Poll the Flemish detector feed on a fixed interval, around the clock. Start with: python scripts/run_miv_poller.py

Collection never stops at night: the days x hours picture needs all 168 hours of the week.
Interval defaults to 5 minutes (TRAVELSMART_MIV_INTERVAL_MINUTES); the feed itself updates every minute.
"""
import os

from travelsmart.main import Session, miv_collect
from travelsmart.polling import run_forever

def job():
    with Session() as session:
        miv_collect(session)

run_forever("miv", job, max(1, int(os.getenv("TRAVELSMART_MIV_INTERVAL_MINUTES", "5"))))
