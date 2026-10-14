#!/usr/bin/env bash
# Open the modded Minecraft with screen-share, in one command.
#
#   screenshare/play.sh                 start everything and open the game
#   screenshare/play.sh --stop          shut the game and the capture service down
#   screenshare/play.sh --purge         ... and close every screen channel too (see below)
#   screenshare/play.sh --status        what is running right now
#   screenshare/play.sh --world <name>  open a particular world (default: last used)
#   screenshare/play.sh --rebuild       recompile the patched classes first
#
# --stop leaves the screen channels up, because a channel is what holds your app window: closing
# one moves that app back to the real screen. --purge closes them as well, so it is the flag that
# leaves truly nothing behind.
#
# What "everything" means, in order:
#   1. exactly one capture service (a stray second one would race on the manifest)
#   2. every recorded screen channel (app_screens.sh restore) so the per-app screens
#      you set up are back, even though Hyprland renumbers headless outputs
#   3. the patched client, straight into a world if one exists
#
# In game: F7 places a panel where you look, N cycles which source it shows,
# F8 toggles edit mode, +/- resize, ; ' tilt, arrows move, DEL removes, R resets.
set -uo pipefail

SS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SS/.." && pwd)"
GAMEDIR="${MC_SCREENSHARE_GAMEDIR:-$SS/gamedir}"
WORLD_FILE="$GAMEDIR/.last-world"

die() {
  echo "error: $*" >&2
  command -v notify-send >/dev/null && notify-send -u critical "Minecraft screen-share" "$*"
  exit 1
}

client_pids() { pgrep -f "[n]et.minecraft.client.main.Main" || true; }
capture_pids() { pgrep -f "[c]apture_service.py" || true; }

# Virtual screens (a.k.a. channels) are outputs, and an output outlives the app that was on it: it
# stays a compositor surface the game has to composite and a source the capture service keeps
# grabbing. So "what is still running" is not just processes.
virtual_outputs() {
  hyprctl monitors -j 2>/dev/null | python3 -c "
import json, sys
try:
    print('\n'.join(m['name'] for m in json.load(sys.stdin) if m['name'].startswith('HEADLESS')))
except Exception:
    pass" 2>/dev/null
}

channels() {
  python3 -c "
import json, os
p = '$SS/channels.json'
print('\n'.join(json.load(open(p)).keys()) if os.path.exists(p) else '')" 2>/dev/null
}

stop_all() {  # [quiet] - any argument suppresses the "channels are left up" notice (purge's job)
  local quiet="${1:-}" p
  for p in $(client_pids); do kill "$p" 2>/dev/null && echo "stopped client (pid $p)"; done
  for p in $(capture_pids); do kill "$p" 2>/dev/null && echo "stopped capture service (pid $p)"; done
  sleep 1.5
  for p in $(client_pids) $(capture_pids); do kill -9 "$p" 2>/dev/null; done
  local outs; outs="$(virtual_outputs | grep -c . || true)"
  if [ "${outs:-0}" -gt 0 ] && [ -z "$quiet" ]; then
    echo "screen channels are left up (they hold your app windows):"
    for o in $(channels); do echo "  $o"; done
    echo "  $outs virtual screen(s) still cost a compositor surface + a capture source"
    echo "  drop one          : app_screens.sh remove <name>"
    echo "  drop everything   : play.sh --purge"
  fi
}

# --purge: the real "stop everything". Tears the channels down too, which moves their apps back to
# the real screen - that is the trade-off, so it is a separate flag rather than part of --stop.
purge_all() {
  local ch o
  stop_all quiet
  for ch in $(channels); do
    bash "$SS/app_screens.sh" remove "$ch" >/dev/null 2>&1 && echo "closed screen '$ch'"
  done
  for o in $(virtual_outputs); do
    hyprctl output remove "$o" >/dev/null 2>&1 && echo "removed leftover output $o"
  done
  rm -rf /dev/shm/mcscreen
  echo "nothing of the stack is left running (frames cleared, virtual screens gone)"
}

status() {
  local n outs
  n="$(capture_pids | wc -l)"
  echo "capture service : $n running$([ "$n" -gt 1 ] && echo '  <-- more than one, run --stop first')"
  n="$(client_pids | wc -l)"
  echo "minecraft client: $n running"
  outs="$(virtual_outputs | grep -c . || true)"
  echo "virtual screens : ${outs:-0} up$( [ "${outs:-0}" -gt 0 ] && echo "  (each one costs a compositor surface)" )"
  for o in $(virtual_outputs); do echo "                  $o"; done
  if [ -f /dev/shm/mcscreen/sources.json ]; then
    echo
    bash "$SS/app_screens.sh" sources
  else
    echo
    echo "no frame manifest at /dev/shm/mcscreen - the game would find no sources"
  fi
  echo
  bash "$SS/app_screens.sh" list 2>/dev/null
}

start_all() {
  local world="$1" rebuild="$2"

  if [ "$rebuild" = "1" ]; then
    [ -x "$SS/build.sh" ] || die "no screenshare/build.sh to rebuild with"
    echo "rebuilding the patched classes..."
    bash "$SS/build.sh" || die "build failed"
  fi

  if [ "$(capture_pids | wc -l)" -gt 0 ]; then
    echo "restarting the capture service (killing $(capture_pids | wc -l) instance(s))"
    for p in $(capture_pids); do kill "$p" 2>/dev/null; done
    sleep 1
    for p in $(capture_pids); do kill -9 "$p" 2>/dev/null; done
  fi

  # 1. capture service - niced, because it must never take CPU from the compositor or the game
  setsid nohup nice -n 10 python3 "$SS/capture_service.py" --fps 12 --width 640 --height 360 \
    --sources "${MC_SCREENSHARE_SOURCES:-monitors}" > "$SS/capture.log" 2>&1 < /dev/null &
  echo "capture service started (log: $SS/capture.log)"
  local i
  for i in $(seq 1 40); do
    [ -f /dev/shm/mcscreen/sources.json ] && break
    sleep 0.25
  done
  [ -f /dev/shm/mcscreen/sources.json ] || die "the capture service produced no manifest - see $SS/capture.log"

  # 2. screen channels
  echo
  if [ -f "$SS/channels.json" ] && [ "$(python3 -c "
import json;print(len(json.load(open('$SS/channels.json'))))" 2>/dev/null)" != "0" ]; then
    bash "$SS/app_screens.sh" restore
    echo
  fi

  # 3. the client
  if [ -z "$world" ] && [ -f "$WORLD_FILE" ]; then
    world="$(cat "$WORLD_FILE")"
  fi
  if [ -z "$world" ]; then
    world="$(ls "$GAMEDIR/saves" 2>/dev/null | head -1)"
  fi

  rm -f "$SS/client.log"
  if [ -n "$world" ] && [ -d "$GAMEDIR/saves/$world" ]; then
    echo "opening world '$world'"
    echo "$world" > "$WORLD_FILE"
    MC_SCREENSHARE_WORLD="$world" setsid nohup bash "$SS/run_client.sh" \
      > "$SS/client.log" 2>&1 < /dev/null &
  else
    echo "no world found - the game will start at the title screen"
    setsid nohup bash "$SS/run_client.sh" > "$SS/client.log" 2>&1 < /dev/null &
  fi

  echo "client starting (log: $SS/client.log)"
  for i in $(seq 1 60); do
    sleep 1
    hyprctl clients -j 2>/dev/null | grep -qi minecraft && break
  done
  if hyprctl clients -j 2>/dev/null | grep -qi minecraft; then
    echo "game window is up."
    command -v notify-send >/dev/null && \
      notify-send "Minecraft screen-share" "Game is starting. F7 places a panel, N picks the screen."
  else
    echo "no game window yet - check $SS/client.log (first launch decompresses libs, so give it a moment)"
  fi

  cat <<'EOF'

In game:
  F7   place a panel where you look      N    cycle which source it shows
  F8   edit mode                         +/-  resize the selected panel
  ; '  tilt it                           arrows  move it
  DEL  remove it                         R    reset the layout

Your panels come back on their own: the layout is saved in gamedir/screenshare.json
and a channel's source id (mon-<name>) no longer changes when Hyprland renumbers outputs.
EOF
}

case "${1:-}" in
  --stop)      stop_all ;;
  --purge)     purge_all ;;
  --status|-s) status ;;
  --world)     shift; start_all "${1:-}" 0 ;;
  --rebuild)   start_all "" 1 ;;
  "")          start_all "" 0 ;;
  *)           sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
esac
