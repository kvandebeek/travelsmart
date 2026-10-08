"""Long-running collector for GitHub Actions: one job measures everything that becomes due.

Every minute: pull, measure whatever is due (regular slots incl. corridors, the HERE sample,
the extra streams), push new observations, and ask publish.yml to rebuild the dashboard (at
most every 10 minutes). Once a day after 12:10 it adds historical weather. It stops after
`LOOP_MINUTES` (GitHub jobs may run 6 hours) or when the day's collection windows are over.
Nothing is ever measured twice: the collector skips anything already in the observation files.
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from travelsmart.commute_live import run_tick
from travelsmart.commute_weather import enrich_day

ZONE = ZoneInfo("Europe/Brussels")
END_OF_DAY = (18, 0)                     # last slot 17:30, last extra tick 17:40
MAX_SECONDS = int(os.getenv("LOOP_MINUTES", "340")) * 60
REPO = os.getenv("GITHUB_REPOSITORY", "kvandebeek/travelsmart")
BRANCH = os.getenv("GITHUB_REF_NAME", "master")


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def push(message: str) -> bool:
    git("add", "-A", "observations")
    if git("diff", "--cached", "--quiet").returncode == 0:
        return False
    git("commit", "-q", "-m", message)
    for attempt in range(5):
        if git("pull", "-q", "--rebase", "origin", BRANCH).returncode == 0 and \
           git("push", "-q", "origin", f"HEAD:{BRANCH}").returncode == 0:
            return True
        time.sleep(5 * (attempt + 1))
    print("::warning::could not push observations", flush=True)
    return False


def publish() -> None:
    reply = httpx.post(f"https://api.github.com/repos/{REPO}/actions/workflows/publish.yml/dispatches",
                       headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                                "Accept": "application/vnd.github+json"},
                       json={"ref": BRANCH}, timeout=30)
    print(f"publish requested -> {reply.status_code}", flush=True)


def summary(line: str) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as file:
            file.write(line + "\n\n")


def main() -> None:
    started = time.monotonic()
    last_publish = 0.0
    unpublished = False
    weather_day = None
    measured = 0
    while time.monotonic() - started < MAX_SECONDS:
        now = datetime.now(timezone.utc)
        local = now.astimezone(ZONE)
        if local.weekday() >= 5 or (local.hour, local.minute) >= END_OF_DAY:
            print(f"{local:%H:%M} collection windows over for today", flush=True)
            break
        git("pull", "-q", "--rebase", "origin", BRANCH)          # see what other runs measured
        for options in ({}, {"provider": "here"}, {"stream": "extra"}):
            try:
                result = run_tick(now, execute=True, **options)
            except Exception as error:  # keep the loop alive; never print messages (URLs hold keys)
                print(f"{local:%H:%M} {options or 'regular'} failed: {type(error).__name__}", flush=True)
                continue
            if result.get("attempted"):
                measured += result["attempted"]
                print(f"{local:%H:%M} {options or 'regular'}: {result}", flush=True)
                summary(f"✅ {local:%H:%M} {options.get('stream', options.get('provider', 'regular'))}: "
                        f"{result['stored']} of {result['attempted']} measured")
        if (local.hour, local.minute) >= (12, 10) and weather_day != local.date():
            weather_day = local.date()
            try:
                print(f"weather: {enrich_day(local.date() - timedelta(days=5))}", flush=True)
            except Exception as error:
                print(f"weather failed: {type(error).__name__}", flush=True)
        if push(f"Record commute observations ({local:%H:%M})"):
            unpublished = True
        if unpublished and time.monotonic() - last_publish >= 600:
            publish()
            last_publish, unpublished = time.monotonic(), False
        time.sleep(max(5, 65 - datetime.now().second))           # just after the next minute starts
    if push("Record commute observations (end of loop)") or unpublished:
        publish()
    summary(f"Loop finished: {measured} measurements in {round((time.monotonic() - started) / 60)} minutes")


if __name__ == "__main__":
    main()
