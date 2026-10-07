"""Run scheduled configured collections. Start with: python scripts/run_scheduler.py"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from apscheduler.schedulers.blocking import BlockingScheduler
from travelsmart.config import load_settings
from travelsmart.main import Session
from travelsmart.collectors import collect_many
from travelsmart.providers import OSRMProvider

settings = load_settings(); provider = OSRMProvider()
def due_collection():
    now = datetime.now(ZoneInfo(settings.schedules.get("timezone", "Europe/Brussels")))
    for window in settings.schedules.get("windows", []):
        if now.weekday() in window["weekdays"] and window["start"] <= now.strftime("%H:%M") <= window["end"]:
            if now.minute % window["interval_minutes"] == 0:
                ids = [j.id for j in settings.journeys.values() if j.active]
                with Session() as s: rows, failed = collect_many(s, settings, provider, ids)
                logging.info("tick %s: stored=%s failed=%s", now.strftime("%H:%M"), len(rows), len(failed))
scheduler = BlockingScheduler(timezone=settings.schedules.get("timezone", "Europe/Brussels"))
scheduler.add_job(due_collection, "cron", minute="*", max_instances=1, coalesce=True)  # a slow tick never overlaps the next
scheduler.start()
