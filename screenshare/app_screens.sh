#!/usr/bin/env bash
# Give each application its own screen, for the Minecraft mod to put on a panel.
#
# WHY THIS EXISTS
# ---------------
# The capture service can already publish one source per application window
# (`--sources windows`), but those are *region* captures: `grim -g <rect>` returns
# whatever the compositor shows at that rectangle. Measured 2026-10-14 on this host,
# four maximised windows all reported the same rectangle (1908x1023 at 6,51) and all
# four sources came back as the SAME PICTURE, so "one screen per app" was cosmetic.
#
# A window alone on its own output has no such problem: it is the only thing there, so
# capturing that output *is* capturing the application, occlusion-free. The capture
# service already enumerates monitors, so such a channel shows up in the game with no
# changes on the Java side.
#
# The channel's source id is `mon-<name>` (from channels.json), NOT `mon-<output>`:
# Hyprland renumbers headless outputs on every restart, and an id built from the output
# name would move out from under a panel already placed in the world.
#
# USAGE
#   app_screens.sh windows                       what you could put on a screen
#   app_screens.sh attach <name> <class>         use a window ALREADY open (no relaunch)
#   app_screens.sh add  <name> -- <command...>   new screen + launch an app on it
#   app_screens.sh move <name> [app-class]       send a window to that screen
#   app_screens.sh restore                       recreate screens after a reboot
#   app_screens.sh list                          screens, outputs and the game source id
#   app_screens.sh remove <name>                 tear a screen down
#   app_screens.sh sources                       what the game can currently place
#
# `attach` is usually what you want: it takes an application you already have running and
# gives it its own screen, without launching a second copy or a separate profile. If two
# windows of that class match (two Chrome windows, say) it refuses to guess and prints them,
# so add --title <part of the window title> to choose.
#
# In game: F7 places a panel where you look, N cycles its source to this channel.
#
# Hyprland 0.56 notes, all measured:
#   * Dispatchers are Lua expressions: hyprctl dispatch 'hl.dsp.window.move({ workspace = 5 })'
#     The older `hyprctl dispatch movetoworkspacesilent 5` form is a syntax error.
#   * window.move acts on the FOCUSED window, and the by-address selector forms are
#     rejected by this build - so windows are focused by parking the pointer inside them
#     (input:follow_mouse), which is why moving one needs no clicking.
set -uo pipefail

SS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE="$SS/channels.json"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-1}"

die() { echo "error: $*" >&2; exit 1; }

monitors() { hyprctl monitors -j 2>/dev/null; }
clients() { hyprctl clients -j 2>/dev/null; }

output_names() {
  monitors | python3 -c "import json,sys;print(' '.join(m['name'] for m in json.load(sys.stdin)))"
}

output_workspace() {  # <output> -> its active workspace id
  monitors | python3 -c "
import json,sys
for m in json.load(sys.stdin):
    if m['name'] == sys.argv[1]:
        print(m.get('activeWorkspace', {}).get('id', '')); break" "$1"
}

real_workspace() {  # workspace of the first non-headless output
  monitors | python3 -c "
import json,sys
for m in json.load(sys.stdin):
    if not m['name'].startswith('HEADLESS'):
        print(m.get('activeWorkspace', {}).get('id', '')); break"
}

load_state() { [ -f "$STATE" ] && cat "$STATE" || echo '{}'; }
save_state() { local tmp="$STATE.tmp.$$"; cat > "$tmp" && mv "$tmp" "$STATE"; }

state_get() {  # <name> <field>
  load_state | python3 -c "
import json,sys
e = json.load(sys.stdin).get(sys.argv[1]) or {}
print(e.get(sys.argv[2], ''))" "$1" "$2"
}

# ------------------------------------------------------------------ window plumbing

cursor_pos() { hyprctl cursorpos 2>/dev/null | tr -d ' '; }

cursor_to() {  # <x,y>
  [ -n "${1:-}" ] && ydotool mousemove --absolute "${1%,*}" "${1#*,}" 2>/dev/null
  return 0
}

# There is deliberately no "focus this window by moving the pointer" helper here any more.
# It looked reasonable and worked by luck when the target happened to be focused already,
# but ydotool --absolute does not land where you compute (uinput absolute axes), so
# window.move - which acts on the focused window - moved one of the user's Chrome windows
# instead of the target. Windows are addressed by selector now (see move_focused_window).

find_channel_client() {  # <channel name> -> that channel's OWN window json, or nothing
  # Every Chrome window shares the class "google-chrome", so class alone would happily
  # match the user's own browser and move it to a hidden screen. The channel's own
  # instance is the one whose process cmdline carries the user-data-dir it was launched
  # with, so we match on /proc/<pid>/cmdline instead.
  local marker
  marker="$(load_state | python3 -c "
import json,sys
cmd = (json.load(sys.stdin).get(sys.argv[1]) or {}).get('command') or []
for a in cmd:
    if a.startswith('--user-data-dir='):
        print(a.split('=', 1)[1]); break" "$1")"
  [ -n "$marker" ] || return 0
  clients | python3 -c "
import json,sys
marker = sys.argv[1]
for c in json.load(sys.stdin):
    if not c.get('mapped') or not c.get('pid'):
        continue
    try:
        cmdline = open('/proc/%d/cmdline' % c['pid'], 'rb').read().decode('utf8', 'replace')
    except OSError:
        continue
    if marker in cmdline:
        print(json.dumps(c)); break" "$marker"
}

client_classes_on() {  # <output> -> classes of the windows living there
  clients | python3 -c "
import json,sys,subprocess
out = sys.argv[1]
try:
    mons = {m['id']: m['name'] for m in json.loads(subprocess.check_output(['hyprctl','monitors','-j']))}
except Exception:
    raise SystemExit
print(' '.join(c.get('class', '?') for c in json.load(sys.stdin) if mons.get(c.get('monitor')) == out))" "$1"
}

candidates() {  # windows a person might reasonably put on a screen, one json per line
  clients | python3 -c "
import json,sys,subprocess
try:
    mons = {m['id']: m['name'] for m in json.loads(subprocess.check_output(['hyprctl','monitors','-j']))}
except Exception:
    mons = {}
for c in json.load(sys.stdin):
    if not c.get('mapped'):
        continue
    w, h = c.get('size', [0, 0])
    if w < 200 or h < 200:
        continue                        # dialogs, tooltips and slivers are not screens
    if 'minecraft' in (c.get('class') or '').lower():
        continue                        # never mirror the game into itself
    c['_monitor'] = mons.get(c.get('monitor'), '?')
    print(json.dumps(c))"
}

pick_window() {  # <class-substring> <title-substring> [address-prefix] -> one window json
  # rc 0 = picked, 3 = several match, 4 = none. Never returns a guess: every Chrome
  # window reports class "google-chrome", and moving the wrong one would take the
  # user's main browser off their screen. An address prefix settles it exactly - the
  # `windows` listing prints each one for this purpose.
  candidates | python3 -c "
import json,sys
want, title, addr = sys.argv[1].lower(), sys.argv[2].lower(), sys.argv[3].lower().removeprefix('0x')
rows = [json.loads(l) for l in sys.stdin if l.strip()]
if addr:
    rows = [c for c in rows if (c.get('address') or '').lower().removeprefix('0x').startswith(addr)]
if want:
    rows = [c for c in rows if want in (c.get('class') or '').lower()]
if title:
    rows = [c for c in rows if title in (c.get('title') or '').lower()]
if not want and not title and not addr:
    rows = [c for c in rows if c.get('focusHistoryID') == 0][:1] or rows[:1]
if len(rows) == 1:
    print(json.dumps(rows[0])); raise SystemExit(0)
if not rows:
    print('  no open window matches that', file=sys.stderr); raise SystemExit(4)
print(f'  {len(rows)} windows match - add --title <part of the title> or --address <prefix>:', file=sys.stderr)
for c in rows:
    w, h = c.get('size', [0, 0])
    print(f\"    {c.get('class') or '?':<18} {str(w) + 'x' + str(h):<11} {(c.get('address') or '').removeprefix('0x'):<14} \"
          f\"{(c.get('title') or '')[:40]}\", file=sys.stderr)
raise SystemExit(3)" "$1" "$2" "${3:-}"
}

# Hyprland renumbers headless outputs, so the name is always *discovered*, never assumed.
new_output() {
  local before after name
  before=" $(output_names) "
  hyprctl output create headless >/dev/null 2>&1 || die "could not create a headless output"
  for _ in $(seq 1 20); do
    sleep 0.25
    after="$(output_names)"
    for n in $after; do
      case "$before" in *" $n "*) ;; *) name="$n";; esac
    done
    if [ -n "${name:-}" ]; then
      shrink_output "$name"
      echo "$name"
      return 0
    fi
  done
  die "the new output never appeared in hyprctl monitors"
}

shrink_output() {  # <output name> - make the virtual screen cheap
  # A headless output is not free: it is a real compositor surface, composited every frame,
  # holding GBM buffers - and on this machine the iGPU has no memory of its own, so those
  # buffers come out of the same system RAM the game is about to want. grim also copies every
  # pixel of it, 12 times a second, per source.
  #
  # The in-world panel can only ever show 640x360 (the capture size), so a 1920x1080 output is
  # 9x more pixels than can ever be displayed. 1280x720 keeps 2x headroom over the panel at 44%
  # of the compositing, buffer and copy cost. Override with MC_SCREENSHARE_CHANNEL_MODE=<WxH@R>
  # if you want a full-size virtual screen.
  local mode="${MC_SCREENSHARE_CHANNEL_MODE:-1280x720@60}" got
  hyprctl eval "hl.monitor({ output = \"$1\", mode = \"$mode\" })" >/dev/null 2>&1 || true
  sleep 0.4
  got="$(hyprctl monitors -j | python3 -c "
import json,sys
for m in json.load(sys.stdin):
    if m['name'] == '$1': print('{}x{}'.format(m['width'], m['height'])); break" 2>/dev/null)"
  [ -n "$got" ] && echo "  virtual screen $1 sized $got (panel shows 640x360)" >&2
}

move_focused_window() {  # <address> <workspace> [expected-output] -> move ONE named window
  # Hyprland 0.56 accepts a window *selector* here, so we can name the exact window:
  #   hl.dsp.window.move({ workspace = 4, window = "address:0x..." })
  # This replaces focusing the window first, which was unreliable: the intended focus came
  # from parking the pointer inside the window, ydotool's --absolute does not land where you
  # compute (uinput absolute axes), so window.move acted on whatever was focused instead -
  # during testing that moved one of the user's Chrome windows rather than the target.
  local addr="${1#0x}" ws="$2" expect="${3:-}" err where
  err="$(hyprctl dispatch "hl.dsp.window.move({ workspace = $ws, window = \"address:0x$addr\" })" 2>&1 | head -1)"
  sleep 0.7
  if [ -n "$expect" ]; then
    where="$(client_monitor "$addr")"
    if [ "$where" != "$expect" ]; then
      echo "  WARNING: the move did not take - window is on '${where:-nowhere}', wanted '$expect'  ${err}" >&2
      return 1
    fi
  fi
  return 0
}

client_monitor() {  # <address> -> the output it is on right now
  clients | python3 -c "
import json,sys,subprocess
addr = sys.argv[1].lower().removeprefix('0x')
try:
    mons = {m['id']: m['name'] for m in json.loads(subprocess.check_output(['hyprctl','monitors','-j']))}
except Exception:
    raise SystemExit
for c in json.load(sys.stdin):
    if (c.get('address') or '').lower().removeprefix('0x') == addr:
        print(mons.get(c.get('monitor'), '')); break" "$1"
}

address_of() {  # <client json> -> its address without the 0x
  echo "$1" | python3 -c "import json,sys;print((json.load(sys.stdin).get('address') or '').lower().removeprefix('0x'))"
}

active_address() {  # the focused window right now, which is unambiguous
  hyprctl activewindow -j 2>/dev/null | python3 -c "
import json,sys
try:
    w = json.load(sys.stdin)
except Exception:
    raise SystemExit
print((w.get('address') or '').lower().removeprefix('0x'))"
}

# ------------------------------------------------------------------ launching

launch_on() {  # <name> <output> -- <command...>
  local name="$1" out="$2"; shift 2
  [ "${1:-}" = "--" ] && shift
  local ws back landed="" saved_cursor
  ws="$(output_workspace "$out")"
  back="$(real_workspace)"
  saved_cursor="$(cursor_pos)"

  # Route the new window here by focusing this output first. Crucially we must wait for
  # the window to actually MAP before handing focus back: a browser can take seconds to
  # appear, and restoring focus first sends the window to the real screen instead.
  hyprctl dispatch "hl.dsp.focus({ workspace = $ws })" >/dev/null 2>&1
  sleep 0.4
  setsid nohup "$@" >/dev/null 2>&1 &
  local pid=$!
  for _ in $(seq 1 40); do
    sleep 0.5
    landed="$(client_classes_on "$out" | awk '{print $1}')"
    [ -n "$landed" ] && break
  done
  hyprctl dispatch "hl.dsp.focus({ workspace = $back })" >/dev/null 2>&1
  cursor_to "$saved_cursor"

  if [ -n "$landed" ]; then
    echo "  launched: $*  (pid $pid); window '$landed' is on $out"
  else
    echo "  launched: $*  (pid $pid)"
    echo "  WARNING: nothing mapped on $out within 20 s - start the app, then run:"
    echo "           app_screens.sh move $name"
  fi

  load_state | python3 -c "
import json,sys
d = json.load(sys.stdin)
old = d.get(sys.argv[1]) or {}
d[sys.argv[1]] = {'output': sys.argv[2], 'workspace': int(sys.argv[3]),
                  'app_class': sys.argv[4] or old.get('app_class', ''),
                  'launched_pid': int(sys.argv[5]) if sys.argv[5].isdigit() else None,
                  'command': sys.argv[6:]}
json.dump(d, sys.stdout, indent=2)" "$name" "$out" "$ws" "$landed" "$pid" "$@" | save_state
}

stop_launched_app() {  # <channel name> - stop the app THIS channel launched, and nothing else
  # A channel made with 'add' launched an app of its own, so closing the channel should close that
  # app. Without this, every channel ever created leaves a stray process and window behind - which
  # is how a machine ends up with several browsers open and nobody who remembers starting them.
  # A channel made with 'attach' records no launched_pid, so the user's own app is never touched.
  local name="$1" pid first
  pid="$(state_get "$name" launched_pid)"
  case "$pid" in ''|None|*[!0-9]*) return 0 ;; esac
  [ -r "/proc/$pid/cmdline" ] || return 0                  # already exited
  first="$(state_get "$name" command | tr -d '[]' | cut -d, -f1 | tr -d " '\"")"
  # Guard against the pid having been reused by something else since.
  [ -z "$first" ] || grep -qa -- "$first" "/proc/$pid/cmdline" || return 0
  kill "$pid" 2>/dev/null && echo "  stopped its app (pid $pid)"
  return 0
}

# ------------------------------------------------------------------ commands

cmd_add() {  # add <name> -- <command...>
  local name="${1:-}"; shift || true
  [ -n "$name" ] || die "usage: app_screens.sh add <name> -- <command...>"
  [ "${1:-}" = "--" ] && shift
  [ $# -gt 0 ] || die "no command given after --"
  [ -z "$(state_get "$name" output)" ] || die "screen '$name' already exists"

  local out; out="$(new_output)"
  echo "screen '$name' -> output $out"
  launch_on "$name" "$out" -- "$@"
  echo "  in game: place a panel and press N until it reads mon-$name"
}

cmd_windows() {
  echo "windows you could put on a screen:"
  printf '  %-18s %-11s %-14s %-13s %s\n' "class" "size" "address" "currently on" "title"
  local n=0 line
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    n=$((n + 1))
    echo "$line" | python3 -c "
import json,sys
c = json.load(sys.stdin)
w, h = c.get('size', [0, 0])
print(f\"  {c.get('class') or '?':<18} {str(w) + 'x' + str(h):<11} \"
      f\"{(c.get('address') or '').removeprefix('0x'):<14} {c['_monitor']:<13} \"
      f\"{(c.get('title') or '')[:40]}\")"
  done < <(candidates)
  [ "$n" -gt 0 ] || echo "  (nothing big enough is open)"
  echo
  echo "attach one to its own screen:  app_screens.sh attach <name> <class-substring>"
  echo "  two windows of the same class?  add --title <part of the title> or --address <prefix>"
  echo "  it refuses to guess rather than move the wrong window."
}

cmd_attach() {  # attach <name> [class-substring] [--title <substring>]
  local name="${1:-}"; shift || true
  [ -n "$name" ] || die "usage: app_screens.sh attach <name> [class-substring] [--title <substring>]"
  [ -z "$(state_get "$name" output)" ] || die "screen '$name' already exists"

  local want="" title="" addr=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --title)   title="${2:-}"; shift 2 ;;
      --address) addr="${2:-}"; shift 2 ;;
      *) [ -z "$want" ] && want="$1"; shift ;;
    esac
  done

  # No selector at all means "the window I am looking at", read from the compositor rather than
  # guessed at. A class or a title is a selector too: pick_window resolves those, so only reach for
  # the focused window when nothing at all was given. (Requiring an address here made the class and
  # title paths unreachable - `attach music google-chrome --title "..."` died with "no focused
  # window to attach" even though it named exactly what it wanted.)
  if [ -z "${want}${title}${addr}" ]; then
    addr="$(active_address)"
    [ -n "$addr" ] || die "no focused window to attach - or name one: attach <name> <class>"
  fi

  # Pick the window that is ALREADY OPEN - no new process, no second profile. stderr is
  # left visible so an ambiguous match prints the candidates for the user to choose from.
  local pick rc=0
  pick="$(pick_window "$want" "$title" "$addr")" || rc=$?
  case "$rc" in
    3) die "several windows match - pick one with --title (see: app_screens.sh windows)" ;;
    4) die "no open window matches '${want:-anything}' (see: app_screens.sh windows)" ;;
  esac
  [ -n "$pick" ] || die "could not pick a window"

  local cls ttl out ws
  cls="$(echo "$pick" | python3 -c "import json,sys;print(json.load(sys.stdin).get('class') or '')")"
  ttl="$(echo "$pick" | python3 -c "import json,sys;print((json.load(sys.stdin).get('title') or '')[:40])")"

  out="$(new_output)"
  ws="$(output_workspace "$out")"
  if ! move_focused_window "$(address_of "$pick")" "$ws" "$out"; then
    hyprctl output remove "$out" >/dev/null 2>&1   # do not leave a stray output behind
    die "could not move that window - nothing was changed"
  fi

  load_state | python3 -c "
import json,sys
d = json.load(sys.stdin)
d[sys.argv[1]] = {'output': sys.argv[2], 'workspace': int(sys.argv[3]),
                  'attached': True, 'app_class': sys.argv[4], 'title': sys.argv[5],
                  'command': []}
json.dump(d, sys.stdout, indent=2)" "$name" "$out" "$ws" "$cls" "$ttl" | save_state

  echo "screen '$name' -> output $out"
  echo "  attached the window already open: '$cls'  \"$ttl\""
  echo "  in game: press N until a panel reads mon-$name"
  echo "  (nothing was launched, so your existing browser session is untouched)"
}

cmd_move() {  # move <name> [class-substring] [--title <substring>] [--address <prefix>]
  local name="${1:-}"; shift || true
  [ -n "$name" ] || die "usage: app_screens.sh move <name> [class] [--title ...] [--address ...]"
  local out ws; out="$(state_get "$name" output)"
  [ -n "$out" ] || die "no screen called '$name' (see: app_screens.sh list)"
  ws="$(output_workspace "$out")"
  [ -n "$ws" ] || die "screen '$name' points at output '$out', which no longer exists"

  local want="" title="" addr=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --title)   title="${2:-}"; shift 2 ;;
      --address) addr="${2:-}"; shift 2 ;;
      *) [ -z "$want" ] && want="$1"; shift ;;
    esac
  done
  local pick rc=0
  pick="$(pick_window "$want" "$title" "$addr")" || rc=$?
  case "$rc" in
    3) die "several windows match - pick one with --title or --address" ;;
    4) die "no open window matches '${want:-that}'" ;;
  esac
  local cls; cls="$(echo "$pick" | python3 -c "import json,sys;print(json.load(sys.stdin).get('class') or '?')")"
  if move_focused_window "$(address_of "$pick")" "$ws" "$out"; then
    echo "moved the '$cls' window to screen '$name' (output $out, workspace $ws)"
  else
    die "the move did not take - nothing changed"
  fi
}

cmd_restore() {
  local names; names="$(load_state | python3 -c "
import json,sys
print(' '.join(sorted(json.load(sys.stdin))))")"
  [ -n "$names" ] || { echo "no screens recorded yet (see: app_screens.sh add)"; return 0; }

  for name in $names; do
    local out cls
    out="$(state_get "$name" output)"
    cls="$(state_get "$name" app_class)"
    echo "screen '$name':"
    local present=" $(output_names) "
    case "$present" in
      *" $out "*) echo "  output $out still present" ;;
      *)
        echo "  output '$out' is gone, recreating"
        out="$(new_output)"
        local ws; ws="$(output_workspace "$out")"
        load_state | python3 -c "
import json,sys
d = json.load(sys.stdin)
d[sys.argv[1]]['output'] = sys.argv[2]
d[sys.argv[1]]['workspace'] = int(sys.argv[3])
json.dump(d, sys.stdout, indent=2)" "$name" "$out" "$ws" | save_state
        echo "  -> new output $out (source id stays mon-$name)" ;;
    esac

    # Bring the application back: reuse a window that is already open, else relaunch it.
    local live; live="$(client_classes_on "$out")"
    if [ -n "$live" ]; then
      echo "  '$live' already on it"
      continue
    fi
    local own; own="$(find_channel_client "$name")"
    if [ -n "$own" ]; then
      if move_focused_window "$(address_of "$own")" "$(output_workspace "$out")" "$out"; then
        echo "  moved this channel's own '${cls:-window}' window back onto it"
      else
        echo "  could not move its window back - see the warning above"
      fi
      continue
    fi

    # An attached channel has no command of its own: it just wants its app moved back onto
    # the output. Ambiguity is never resolved by guessing - if two windows match, moving the
    # wrong one would take the user's main browser off the screen.
    if [ "$(state_get "$name" attached)" = "True" ]; then
      local pick rc=0 title
      title="$(state_get "$name" title)"
      pick="$(pick_window "$cls" "$title" 2>/dev/null)" || rc=$?
      if [ "$rc" = "0" ] && [ -n "$pick" ]; then
        if move_focused_window "$(address_of "$pick")" "$(output_workspace "$out")" "$out"; then
          echo "  moved the open '$cls' window back onto it"
        else
          echo "  could not move it back - see the warning above"
        fi
      else
        echo "  its app is not open (or several windows match) - start it, then run:"
        echo "           app_screens.sh attach $name ${cls:-<class>}"
      fi
      continue
    fi

    # relaunch the recorded command
    local argv=() part
    while IFS= read -r -d '' part; do argv+=("$part"); done < <(
      load_state | python3 -c "
import json,sys
cmd = (json.load(sys.stdin).get(sys.argv[1]) or {}).get('command') or []
import pathlib
sys.stdout.write('\0'.join(cmd))" "$name")
    if [ ${#argv[@]} -gt 0 ]; then
      launch_on "$name" "$out" -- "${argv[@]}"
    else
      echo "  nothing recorded to relaunch - start the app, then run:"
      echo "           app_screens.sh move $name $cls"
    fi
  done
}

cmd_list() {
  local state; state="$(load_state)"
  [ "$state" = "{}" ] && { echo "no screens yet - see: app_screens.sh add <name> -- <command>"; return 0; }
  python3 - "$STATE" <<'PY'
import json, subprocess, sys
state = json.load(open(sys.argv[1]))
try:
    mons = {m["name"]: m for m in json.loads(subprocess.check_output(["hyprctl", "monitors", "-j"]))}
    wins = json.loads(subprocess.check_output(["hyprctl", "clients", "-j"]))
except Exception:
    mons, wins = {}, []
print("screens:")
for name, e in sorted(state.items()):
    out = e.get("output", "?")
    alive = "present" if out in mons else "MISSING (run: restore)"
    here = [c.get("class", "?") for c in wins if c.get("monitor") == mons.get(out, {}).get("id")]
    print(f"  {name:<12} output={out:<12} {alive:<22} app={e.get('app_class') or '?':<16}"
          f" windows={here or 'none'}")
    print(f"  {'':<12} game source = mon-{name}")
PY
}

cmd_remove() {  # remove <name>
  local name="${1:-}" out
  [ -n "$name" ] || die "usage: app_screens.sh remove <name>"
  out="$(state_get "$name" output)"
  [ -n "$out" ] || die "no screen called '$name'"
  # Move anything living there back to the real screen before removing the output.
  local back; back="$(real_workspace)"
  hyprctl dispatch "hl.dsp.focus({ workspace = $back })" >/dev/null 2>&1
  hyprctl output remove "$out" >/dev/null 2>&1 && echo "removed output $out"
  # Stop the app this channel launched (never an attached one - see stop_launched_app).
  stop_launched_app "$name"
  load_state | python3 -c "
import json,sys
d = json.load(sys.stdin); d.pop(sys.argv[1], None)
json.dump(d, sys.stdout, indent=2)" "$name" | save_state
  echo "screen '$name' removed (any app that was on it is now on the real screen)"
}

cmd_sources() {
  python3 - <<'PY'
import json
try:
    m = json.load(open("/dev/shm/mcscreen/sources.json"))
except Exception:
    print("no manifest - is the capture service running? (play.sh --status)")
    raise SystemExit
print(f"{len(m['sources'])} source(s) the game can place:")
for s in m["sources"]:
    tag = f"  <-- screen channel '{s['channel']}'" if s.get("channel") else ""
    print(f"  {s['id']:<18} {s['native_w']}x{s['native_h']:<6} -> {s['out_w']}x{s['out_h']}"
          f"  {s['fps']:>5}fps alive={s['alive']}{tag}")
PY
}

case "${1:-}" in
  windows|ls) cmd_windows ;;
  attach)  shift; cmd_attach "$@" ;;
  add)     shift; cmd_add "$@" ;;
  move)    shift; cmd_move "$@" ;;
  restore) cmd_restore ;;
  list)    cmd_list ;;
  remove)  shift; cmd_remove "$@" ;;
  sources) cmd_sources ;;
  *) sed -n '3,44p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
esac
