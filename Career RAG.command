#!/usr/bin/env bash
# Double-click to start the Career RAG query interface as its own window.
# Close that window (the red button, top-left) to stop the server and run
# maintenance (step 10: re-index, refresh the outreach gap report)
# automatically -- no separate "stop" step needed.
#
# NOTE on how this actually works, found out the hard way: Chrome's --app
# mode does NOT quit its process just because its one window was closed --
# it silently keeps running in the background with zero windows, forever.
# `open -W` (wait for the app to exit) never returns in that case. So this
# script instead POLLS (via System Events) for the window count on this
# specific Chrome instance hitting zero, and then force-quits it itself.
# First run may prompt for Accessibility permission (System Settings >
# Privacy & Security > Accessibility) -- needed to check the window count.

set -u
cd "$(dirname "$0")/career_index" || { echo "career_index/ not found next to this script."; exit 1; }

PORT=5057
URL="http://127.0.0.1:$PORT"
WALL_PORT=5058
WALL_URL="http://127.0.0.1:$WALL_PORT"
CHROME="/Applications/Google Chrome.app"
PROFILE_DIR="/tmp/career-rag-chrome-profile-$$"

if [ ! -d "$CHROME" ]; then
  echo "Google Chrome isn't installed at /Applications/Google Chrome.app -- open $URL manually in any browser instead, then run maintenance.py by hand when done."
  exit 1
fi

already_running() {
  curl -s -o /dev/null -m 1 "$URL"
}

wall_running() {
  curl -s -o /dev/null -m 1 "$WALL_URL"
}

STARTED_SERVER=0
if already_running; then
  echo "Server already running at $URL -- reusing it."
else
  echo "Starting the query interface..."
  .venv/bin/python webui.py > /tmp/career_webui.log 2>&1 &
  WEBUI_PID=$!
  STARTED_SERVER=1

  for _ in $(seq 1 30); do
    already_running && break
    sleep 0.3
  done
  if ! already_running; then
    echo "Server didn't come up -- check /tmp/career_webui.log"
    exit 1
  fi
fi

# The live wall is a second, independent server. It is a convenience, not a
# requirement: if it fails to come up the query interface still works, so a
# failure here warns and carries on rather than exiting.
STARTED_WALL=0
WALL_PID=""
if wall_running; then
  echo "Live wall already running at $WALL_URL -- reusing it."
else
  echo "Starting the live job wall..."
  .venv/bin/python livewall.py > /tmp/career_livewall.log 2>&1 &
  WALL_PID=$!
  STARTED_WALL=1

  for _ in $(seq 1 30); do
    wall_running && break
    sleep 0.3
  done
  if ! wall_running; then
    echo "Live wall didn't come up -- check /tmp/career_livewall.log. Continuing without it."
    STARTED_WALL=0
  fi
fi

echo "Opening window -- close it to stop and run maintenance."
open -n -a "$CHROME" --args --app="$URL" --user-data-dir="$PROFILE_DIR"

# Find the actual Chrome process for THIS launch (matches on the unique
# --user-data-dir so it can't be confused with any other Chrome window/tab
# already open on the machine).
CHROME_PID=""
for _ in $(seq 1 20); do
  CHROME_PID=$(pgrep -f "Google Chrome --app=$URL --user-data-dir=$PROFILE_DIR" | head -1)
  [ -n "$CHROME_PID" ] && break
  sleep 0.3
done

if [ -z "$CHROME_PID" ]; then
  echo "Couldn't find the Chrome window to watch -- close it yourself, then run: .venv/bin/python maintenance.py"
else
  # Poll until the window is closed (count reaches 0) or the process exits
  # on its own. Two-second interval -- this is a "waiting for a person to
  # finish reading a webpage" loop, not a tight one.
  #
  # Fifth audit round found this flaky: osascript against a live Chrome
  # window intermittently threw AppleScript errors ("Invalid index") or
  # returned an empty result even while the window was genuinely still
  # open. The original version treated an EMPTY/failed read the same as a
  # confirmed zero-window count -- a transient hiccup could force-kill a
  # session mid-use. Fixed two ways: an empty/error reading no longer
  # counts as evidence of anything (just retry), and a real "0" has to
  # show up twice in a row before it's trusted -- one bad reading can't
  # trigger the kill.
  ZERO_STREAK=0
  while kill -0 "$CHROME_PID" 2>/dev/null; do
    sleep 2
    WIN_COUNT=$(osascript -e "tell application \"System Events\" to tell (first process whose unix id is $CHROME_PID) to count windows" 2>/dev/null)
    if [ "$WIN_COUNT" = "0" ]; then
      ZERO_STREAK=$((ZERO_STREAK + 1))
      [ "$ZERO_STREAK" -ge 2 ] && break
    else
      ZERO_STREAK=0   # a real non-zero reading, or an inconclusive/errored one -- reset either way
    fi
  done
  # The window is closed but Chrome's process lingers in the background on
  # its own (confirmed: this isn't a hunch, it's what actually happens) --
  # force-quit this specific isolated instance. Never touches any other
  # Chrome window/profile the user has open elsewhere.
  kill "$CHROME_PID" 2>/dev/null
fi
sleep 1   # let Chrome finish releasing its own lock files before cleanup
rm -rf "$PROFILE_DIR" 2>/dev/null

echo ""
echo "Window closed. Running maintenance..."
if [ "$STARTED_SERVER" = "1" ]; then
  kill "$WEBUI_PID" 2>/dev/null
  wait "$WEBUI_PID" 2>/dev/null
fi
if [ "$STARTED_WALL" = "1" ] && [ -n "$WALL_PID" ]; then
  kill "$WALL_PID" 2>/dev/null
  wait "$WALL_PID" 2>/dev/null
fi

# Fifth audit round: this used to always print "Done" regardless of what
# maintenance.py actually did -- a crashed run (visible traceback above)
# still ended with a false "Done" message.
.venv/bin/python maintenance.py
MAINT_STATUS=$?

echo ""
if [ "$MAINT_STATUS" -eq 0 ]; then
  echo "Done. Press Return to close this window."
else
  echo "Maintenance failed (see the error above). Press Return to close this window."
fi
read -r
