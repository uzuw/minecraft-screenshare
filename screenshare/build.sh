#!/usr/bin/env bash
# Build the patched client: regenerate the hooked Gui, then compile our classes against the game.
#
# Usage: build.sh
# Output: patched/out (first entry on the runtime classpath, so it shadows the game's classes)
set -euo pipefail

# Repo root: the directory this script lives under. Override with MC_ROOT when the
# mod source sits outside the Minecraft workspace it patches.
ROOT="${MC_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SRC="$ROOT/patched/src"
OUT="$ROOT/patched/out"
CP_FILE="$ROOT/notes/classpath.txt"
DECOMPILED="$ROOT/notes/decompiled/net.minecraft.client.gui.Gui/net/minecraft/client/gui/Gui.java"
DECOMPILED_LEVEL="$ROOT/notes/decompiled/net.minecraft.client.renderer.LevelRenderer/net/minecraft/client/renderer/LevelRenderer.java"
DECOMPILED_CMAIN="$ROOT/notes/decompiled/net.minecraft.client.main.Main/net/minecraft/client/main/Main.java"
DECOMPILED_SMAIN="$ROOT/notes/decompiled/net.minecraft.server.Main/net/minecraft/server/Main.java"
MOJANG_JAVA="$HOME/.minecraft/runtime/java-runtime-epsilon/linux/java-runtime-epsilon/bin"

# Mojang ships a JDK 25, but its bin/* are not executable (mode 644), so compile with the
# system javac and target the game's toolchain: MCP-Reborn builds 26.x with Java 25, and the
# launcher runtime is Java 25, which rejects anything newer than class file version 69.
JAVAC=""
if command -v javac >/dev/null; then
  JAVAC="$(command -v javac)"
elif [ -x "$MOJANG_JAVA/javac" ]; then
  JAVAC="$MOJANG_JAVA/javac"
else
  echo "no javac found (looked in \$PATH and $MOJANG_JAVA)" >&2
  exit 1
fi
RELEASE="--release 25"

if [ ! -f "$CP_FILE" ]; then
  echo "missing $CP_FILE - run: python3 tools/check_launch.py" >&2
  exit 1
fi
if [ ! -f "$DECOMPILED" ]; then
  echo "missing decompiled Gui - run: bash tools/decompile_class.sh net.minecraft.client.gui.Gui" >&2
  exit 1
fi
if [ ! -f "$DECOMPILED_LEVEL" ]; then
  echo "missing decompiled LevelRenderer - run:" >&2
  echo "  bash tools/decompile_class.sh net.minecraft.client.renderer.LevelRenderer" >&2
  exit 1
fi
for p in "$DECOMPILED_CMAIN" "$DECOMPILED_SMAIN"; do
  if [ ! -f "$p" ]; then
    echo "missing decompiled $p - run:" >&2
    echo "  bash tools/decompile_class.sh net.minecraft.client.main.Main net.minecraft.server.Main" >&2
    exit 1
  fi
done

# 1. Regenerate the patched game classes (each script skips an already-patched source).
mkdir -p "$SRC/net/minecraft/client/gui" "$SRC/net/minecraft/client/renderer" \
         "$SRC/net/minecraft/client/main" "$SRC/net/minecraft/server"
python3 "$ROOT/screenshare/patch_gui.py" "$DECOMPILED" "$SRC/net/minecraft/client/gui/Gui.java"
python3 "$ROOT/screenshare/patch_level_renderer.py" "$DECOMPILED_LEVEL" \
  "$SRC/net/minecraft/client/renderer/LevelRenderer.java"
python3 "$ROOT/screenshare/patch_main.py" "$DECOMPILED_CMAIN" \
  "$SRC/net/minecraft/client/main/Main.java" net.minecraft.client.main.Main
python3 "$ROOT/screenshare/patch_main.py" "$DECOMPILED_SMAIN" \
  "$SRC/net/minecraft/server/Main.java" net.minecraft.server.Main

# 2. Compile everything we ship on top of the game.
CP="$(cat "$CP_FILE")"
SOURCES="$(mktemp)"
find "$SRC" -name '*.java' | sort > "$SOURCES"
echo "compiling $(wc -l < "$SOURCES") source file(s) with $("$JAVAC" -version 2>&1)"

rm -rf "$OUT"
mkdir -p "$OUT"
"$JAVAC" -nowarn -encoding UTF-8 $RELEASE -cp "$CP" -d "$OUT" "@$SOURCES"
rm -f "$SOURCES"

echo "built $(find "$OUT" -name '*.class' | wc -l) class file(s) into $OUT"
find "$OUT" -name '*.class' | sed "s|$OUT/||" | sort | sed 's/^/  /'
