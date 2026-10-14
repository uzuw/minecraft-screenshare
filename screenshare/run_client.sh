#!/usr/bin/env bash
# Launch the patched client (own classpath, our classes shadowing the game's).
#
# Usage: run_client.sh [extra Minecraft args...]
#   e.g. run_client.sh --width 1600 --height 900
#
# Offline session (--accessToken 0): enough for singleplayer and for the HUD test.
set -euo pipefail

ROOT="${MC_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
MC="${MC_HOME:-$HOME/.minecraft}"
GAMEDIR="${MC_SCREENSHARE_GAMEDIR:-$ROOT/screenshare/gamedir}"
JAVA="$MC/runtime/java-runtime-epsilon/linux/java-runtime-epsilon/bin/java"
CP_FILE="$ROOT/screenshare/classpath.txt"
NATIVES="$ROOT/screenshare/natives"

if [ ! -d "$ROOT/patched/out" ]; then
  echo "nothing built yet - run: screenshare/build.sh" >&2
  exit 1
fi

# classpath + natives (cheap, idempotent: re-extracts nothing already in place)
python3 "$ROOT/screenshare/prepare_libs.py"

ASSET_INDEX="$(python3 -c "import json;print(json.load(open('$MC/versions/26.2/26.2.json'))['assetIndex']['id'])")"

mkdir -p "$GAMEDIR"

# This build's GLFW is X11-only (no Wayland backend), so the game must run through XWayland:
# unsetting DISPLAY makes GLFW fail with "X11: The DISPLAY environment variable is missing".
# Note that XWayland windows do not receive Wayland virtual-keyboard events, so scripted input
# has to go through ydotool (uinput) rather than wtype - see screenshare/layout_probe.py.
if [ "${MC_SCREENSHARE_WAYLAND:-0}" = "1" ]; then
  unset DISPLAY
fi

if [ "${MC_SCREENSHARE_DEBUG_KEYS:-0}" = "1" ]; then
  JAVA_OPTS_EXTRA="-Duzu.screenshare.debugKeys=true"
else
  JAVA_OPTS_EXTRA=""
fi

# MC_SCREENSHARE_WORLD=<save name> jumps straight into a singleplayer world (handy for tests).
if [ -n "${MC_SCREENSHARE_WORLD:-}" ]; then
  set -- --quickPlaySingleplayer "$MC_SCREENSHARE_WORLD" "$@"
fi

# patched/out is first on the classpath, so our Gui + com.uzu.screenshare.* win over the jar's.
#
# The heap and GC options are deliberate, not defaults. This laptop has 15.7 GB of RAM and the
# desktop (browser, Discord, Spotify, the Qt bar, editors) already holds ~12 GB of it, so swap -
# which here is zram, i.e. compressed RAM - is being used before the game even starts. Once the
# kernel is compressing and decompressing pages to make room, every window on screen stutters,
# including the Qt bar. A 2 GB heap with 512 MB committed up front made that launch spike bigger
# than it needed to be:
#   1536M        ample for this world; the JVM only commits what it uses
#   -Xms256M     commits 256 MB less at launch than the old 512M did
#   SerialGC     no parallel GC worker threads; far smaller footprint at this heap size
#   caps         metaspace and the JIT code cache otherwise grow to whatever they ask for
# Raise the heap with MC_SCREENSHARE_XMX=2G if you free up memory (close browser tabs first).
exec "$JAVA" \
  -Xmx"${MC_SCREENSHARE_XMX:-1536M}" -Xms"${MC_SCREENSHARE_XMS:-256M}" \
  -XX:+UseSerialGC \
  -XX:MaxMetaspaceSize=256M \
  -XX:ReservedCodeCacheSize=128M \
  $JAVA_OPTS_EXTRA \
  -Djava.library.path="$NATIVES" \
  -Dorg.lwjgl.librarypath="$NATIVES" \
  -Djava.awt.headless=false \
  -Dorg.lwjgl.util.NoChecks=true \
  -cp "$(cat "$CP_FILE")" \
  net.minecraft.client.main.Main \
  --gameDir "$GAMEDIR" \
  --assetsDir "$MC/assets" \
  --assetIndex "$ASSET_INDEX" \
  --version 26.2 \
  --versionType release \
  --accessToken 0 \
  --username Uzu \
  --uuid 00000000000000000000000000000001 \
  --width 1280 --height 720 \
  "$@"
