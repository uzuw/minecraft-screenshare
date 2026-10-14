#!/usr/bin/env bash
# Start the whole stack detached: capture service first, then the patched client.
# Both are put in their own session (setsid) so they survive this shell and any
# foreground command timeout in the invoking tool.
#
# Usage: start_stack.sh [--no-client]
#        start_stack.sh --stop      stop the capture service (client too, if running)
#        start_stack.sh --status    what is running right now
set -euo pipefail

ROOT="${MC_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SS="$ROOT/screenshare"
GAMEDIR="${MC_SCREENSHARE_GAMEDIR:-$SS/gamedir}"

stop_all() {
  local p n=0
  for p in $(pgrep -f "[n]et.minecraft.client.main.Main" 2>/dev/null || true); do
    kill "$p" 2>/dev/null && n=$((n + 1))
  done
  for p in $(pgrep -f "[c]apture_service\.py" 2>/dev/null || true); do
    kill "$p" 2>/dev/null && n=$((n + 1))
  done
  sleep 1
  for p in $(pgrep -f "[c]apture_service\.py" 2>/dev/null || true); do kill -9 "$p" 2>/dev/null; done
  rm -rf /dev/shm/mcscreen
  echo "stopped $n process(es); frame files cleared"
  # Screen channels are deliberately left up: a channel is the output an app window is living on,
  # so closing one moves that app back to the real screen mid-session. Use play.sh --purge when you
  # want those gone too.
  local outs
  outs="$(hyprctl monitors -j 2>/dev/null | python3 -c "
import json, sys
print(sum(1 for m in json.load(sys.stdin) if m['name'].startswith('HEADLESS')))" 2>/dev/null || echo 0)"
  [ "${outs:-0}" -gt 0 ] && echo "$outs virtual screen(s) still up - see play.sh --status"
}

status() {
  echo "capture service : $(pgrep -f "[c]apture_service\.py" 2>/dev/null | wc -l) running"
  echo "minecraft client: $(pgrep -f "[n]et.minecraft.client.main.Main" 2>/dev/null | wc -l) running"
  echo "frame files     : $(ls /dev/shm/mcscreen/*.frame 2>/dev/null | wc -l) in /dev/shm/mcscreen"
  echo "virtual screens : $(hyprctl monitors -j 2>/dev/null | python3 -c "
import json, sys
print(sum(1 for m in json.load(sys.stdin) if m['name'].startswith('HEADLESS')))" 2>/dev/null || echo '?')"
}

case "${1:-}" in
  --stop)   stop_all; exit 0 ;;
  --status) status;   exit 0 ;;
esac

# Bracket the pattern so it cannot match the shell running this script (its own cmdline contains
# the text). Same rule everywhere in this directory.
pkill -f "[c]apture_service\.py" 2>/dev/null || true
sleep 0.3

# 1. capture service: one frame file per source, published to /dev/shm/mcscreen
#    Default `monitors` covers your display(s) AND every screen channel, because a channel
#    is just another output (see app_screens.sh). It deliberately excludes region-capture
#    window sources (`all`): those read whatever is on top of a window's rectangle, so every
#    maximised window returns the same picture - useful only when windows do not overlap.
# `nice -n 10`: the capture service exists to serve the game, so it must always lose the CPU
# fight to the compositor and the client. At default priority a capture loop that is copying
# full-screen frames is felt as stutter in every window while Minecraft initialises.
setsid nohup nice -n 10 python3 "$SS/capture_service.py" --fps 12 --width 640 --height 360 \
  --sources "${MC_SCREENSHARE_SOURCES:-monitors}" > "$SS/capture.log" 2>&1 < /dev/null &
echo "capture service started (log: $SS/capture.log)"

# wait for the first frame to land so the client's first poll finds a source
for _ in $(seq 1 40); do
  [ -f /dev/shm/mcscreen/sources.json ] && break
  sleep 0.25
done
echo "sources: $(ls /dev/shm/mcscreen/*.frame 2>/dev/null | wc -l) frame file(s)"

if [ "${1:-}" = "--no-client" ]; then
  exit 0
fi

# 2. the patched client
rm -f "$SS/client.log"
setsid nohup bash "$SS/run_client.sh" > "$SS/client.log" 2>&1 < /dev/null &
echo "client started (log: $SS/client.log, game dir: $GAMEDIR)"
