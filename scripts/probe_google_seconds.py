"""Look for exact durations in a Google Maps directions page (docs/network-design.md §12).

Google's route cards round to whole minutes, which is coarse for a two-minute edge. The page itself
is built from a response that carries the duration in seconds, and §12 leaves open whether that can
be read reliably. This probe opens one route, pulls the embedded state out of the page and reports
which numbers near the card's rounded value look like a duration in seconds.

It changes nothing and measures nothing: it is here to answer that question, once.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from scripts.collect_google_maps import (directions_url, dismiss_consent, visible_routes,
                                         wait_for_route)

# Google writes its state into a few globals; this is the one the directions view is built from.
STATE_SCRIPT = """() => {
  const found = {};
  for (const key of ['APP_INITIALIZATION_STATE', 'APP_OPTIONS', 'WIZ_global_data']) {
    try { if (window[key]) found[key] = JSON.stringify(window[key]).slice(0, 4_000_000); }
    catch (error) { found[key] = 'unreadable: ' + error.message; }
  }
  return found;
}"""


def numbers_near(blob: str, target_seconds: int, tolerance: float = 0.2) -> list[int]:
    """Every integer in the blob that could be this route's duration in seconds."""
    low, high = target_seconds * (1 - tolerance), target_seconds * (1 + tolerance)
    seen = {int(match) for match in re.findall(r"\b\d{2,6}\b", blob)}
    return sorted(value for value in seen if low <= value <= high and value != target_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="50.9307,5.3378")
    parser.add_argument("--destination", default="50.8503,4.3517")
    parser.add_argument("--timeout-seconds", type=int, default=45)
    parser.add_argument("--dump", type=Path, help="write the raw state here for a closer look")
    args = parser.parse_args()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(locale="en-GB", timezone_id="Europe/Brussels",
                                viewport={"width": 1440, "height": 900})
        # The page is drawn from a background call; that answer, not the globals, is where a
        # duration in seconds would live, so every response worth looking at is kept.
        responses: list[tuple[str, str]] = []

        def remember(response):
            url = response.url
            if any(part in url for part in ("/maps/rpc/", "/search?", "/dir/", "batchexecute")):
                try:
                    body = response.text()
                except Exception:
                    return
                if len(body) > 200:
                    responses.append((url.split("?")[0], body))

        page.on("response", remember)
        page.goto(directions_url(args.origin, args.destination), wait_until="domcontentloaded",
                  timeout=args.timeout_seconds * 1000)
        dismiss_consent(page)
        text = wait_for_route(page, args.timeout_seconds, headed=False)
        if not text:
            print("no route card appeared; nothing to compare against", file=sys.stderr)
            browser.close()
            return 1
        routes = visible_routes(page)
        minutes = routes[0]["travel_time_minutes"] if routes else None
        state = page.evaluate(STATE_SCRIPT)
        page.wait_for_timeout(2000)
        browser.close()

    print(f"card says: {text[:90]}")
    if minutes is None:
        print("could not read the card's minutes", file=sys.stderr)
        return 1
    rounded = minutes * 60
    print(f"rounded duration: {minutes} min = {rounded} s\n")
    for key, blob in state.items():
        if not isinstance(blob, str) or blob.startswith("unreadable"):
            print(f"{key}: {blob}")
            continue
        candidates = numbers_near(blob, rounded)
        print(f"{key}: {len(blob):,} chars, {len(candidates)} plausible second-values "
              f"within 20% of {rounded}: {candidates[:20]}")
        if args.dump:
            args.dump.write_text(json.dumps(state, indent=1), encoding="utf-8")
            print(f"  raw state written to {args.dump}")
    print(f"\n{len(responses)} background responses captured")
    for url, body in responses:
        candidates = numbers_near(body, rounded)
        if candidates:
            print(f"  {url}: {len(body):,} chars, candidates {candidates[:12]}")
    print("\nA single tight cluster just off the rounded value is the duration; a wide scatter means "
          "these are distances, coordinates or ids and the parse would not be reliable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
