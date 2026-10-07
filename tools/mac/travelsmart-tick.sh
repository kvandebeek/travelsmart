#!/bin/sh
# Starts both TravelSmart workflows on GitHub ("timer" runs). Run every 5 minutes by launchd.
# The workflows decide in Brussels time what is due; a run with nothing due exits without
# API calls. The GitHub token is read from the macOS Keychain, never from a file.
set -eu

REPO="kvandebeek/travelsmart"
LOG="$HOME/Library/Logs/travelsmart-tick.log"

# Weekdays, 05:00-17:59 Brussels time (covers all regular slots, extra and daytime ticks).
day=$(TZ=Europe/Brussels date +%u)      # 1 = Monday ... 7 = Sunday
hour=$(TZ=Europe/Brussels date +%H)
if [ "$day" -gt 5 ] || [ "$hour" -lt 5 ] || [ "$hour" -gt 17 ]; then
  exit 0
fi

token=$(security find-generic-password -s travelsmart-github -a "$USER" -w 2>/dev/null) || {
  echo "$(date '+%F %T') no token in Keychain (service travelsmart-github)" >> "$LOG"; exit 1; }

for workflow in commutes.yml extra-routes.yml; do
  status=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
    -H "Authorization: Bearer $token" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/$REPO/actions/workflows/$workflow/dispatches" \
    -d '{"ref":"master","inputs":{"timer":true}}')
  echo "$(date '+%F %T') $workflow -> $status" >> "$LOG"
done
