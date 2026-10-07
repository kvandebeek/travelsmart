# Mac timer for TravelSmart

A stop-gap while GitHub's own schedule does not fire: a Mac that is always on starts both
workflows every 5 minutes on weekdays (05:00–17:59 Brussels time). The workflows decide what
is due; runs with nothing due make no API calls and do not republish the dashboard.

## 1. Create a GitHub token (once)

GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens →
Generate new token**:

- **Repository access:** Only select repositories → `kvandebeek/travelsmart`
- **Permissions → Repository → Actions:** Read and write (nothing else)
- **Expiration:** e.g. 90 days

Copy the token. Do not paste it into a chat, a file or the repository.

## 2. Store it in the Keychain

```sh
security add-generic-password -s travelsmart-github -a "$USER" -w
```

macOS prompts for the token (it is not shown on screen and not saved in your shell history).
To replace it later, add `-U` to update the existing entry.

## 3. Install the timer

From this folder (`tools/mac` in a checkout of the repository):

```sh
mkdir -p ~/.travelsmart ~/Library/LaunchAgents
cp travelsmart-tick.sh ~/.travelsmart/ && chmod +x ~/.travelsmart/travelsmart-tick.sh
sed "s|__HOME__|$HOME|g" be.travelsmart.tick.plist > ~/Library/LaunchAgents/be.travelsmart.tick.plist
launchctl load ~/Library/LaunchAgents/be.travelsmart.tick.plist
```

## 4. Check it

```sh
tail -f ~/Library/Logs/travelsmart-tick.log
```

Every 5 minutes (in the window) you should see two lines ending in `-> 204`. On GitHub the runs
appear under **Actions** as "workflow_dispatch". Outside 05:00–17:59 or at weekends the log stays
quiet on purpose.

The Mac must not go to sleep: **System Settings → Energy (or Battery → Options) → Prevent automatic
sleeping when the display is off**. A sleeping Mac skips ticks; the next tick catches up on a
regular slot if it is less than 30 minutes late.

## Stop or remove

```sh
launchctl unload ~/Library/LaunchAgents/be.travelsmart.tick.plist
rm ~/Library/LaunchAgents/be.travelsmart.tick.plist ~/.travelsmart/travelsmart-tick.sh
security delete-generic-password -s travelsmart-github -a "$USER"
```

Once GitHub's own schedule runs reliably, unload the timer. Double starts are harmless (nothing
is measured twice), but they waste a little Actions time.
