"""Poll the Flemish Traffic Center events feed (jams, accidents, roadworks) around the clock.
Start with: python scripts/run_datex_poller.py

Interval defaults to 2 minutes (TRAVELSMART_DATEX_INTERVAL_MINUTES). The feed is republished every minute and
jams can come and go within minutes; the end of a jam is known to within one interval. Each poll downloads
~700 KB (the server does not compress), so 2 minutes is ~500 MB a day.
"""
import os

from travelsmart.main import Session, datex_collect
from travelsmart.polling import run_forever

def job():
    with Session() as session:
        datex_collect(session)

run_forever("datex", job, max(1, int(os.getenv("TRAVELSMART_DATEX_INTERVAL_MINUTES", "2"))))
