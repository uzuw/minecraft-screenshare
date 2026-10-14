#!/usr/bin/env bash
# Install clickable launchers for the patched Minecraft, so opening it does not mean
# remembering a path. Writes the entries and refreshes the launcher database.
#
#   install_desktop.sh            install (or refresh) the entries
#   install_desktop.sh --remove   uninstall them
set -euo pipefail

SS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

PLAY="$SS/play.sh"
STOP="$SS/play.sh --stop"

write_entry() {  # <file> <name> <exec> <icon> <categories> [comment]
  cat > "$APPS/$1" <<EOF
[Desktop Entry]
Type=Application
Name=$2
${6:+Comment=$6}
Exec=$3
Path=$(cd "$SS/.." && pwd)
Icon=$4
Terminal=false
Categories=$5
StartupNotify=true
EOF
  chmod +x "$APPS/$1"
}

if [ "${1:-}" = "--remove" ]; then
  rm -f "$APPS/minecraft-screenshare.desktop" "$APPS/minecraft-screenshare-stop.desktop"
  command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
  echo "removed the launchers"
  exit 0
fi

[ -x "$PLAY" ] || { echo "error: $PLAY is not executable" >&2; exit 1; }
mkdir -p "$APPS"

# No Minecraft icon ships with the game, so the theme falls back to a generic one.
ICON="minecraft"
for candidate in /usr/share/icons/hicolor/*/apps/minecraft.png /usr/share/pixmaps/minecraft.png; do
  [ -f "$candidate" ] && { ICON="$candidate"; break; }
done

write_entry minecraft-screenshare.desktop \
  "Minecraft (screen-share)" "$PLAY" "$ICON" "Game;ActionGame;" \
  "Open the patched Minecraft with in-world screen panels"
write_entry minecraft-screenshare-stop.desktop \
  "Minecraft (screen-share): stop" "$STOP" "process-stop" "Game;" \
  "Stop the game and the capture service"

command -v desktop-file-validate >/dev/null && \
  desktop-file-validate "$APPS/minecraft-screenshare.desktop" \
                        "$APPS/minecraft-screenshare-stop.desktop" \
  && echo "entries validate"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
echo "installed to $APPS:"
echo "  Minecraft (screen-share)         -> $PLAY"
echo "  Minecraft (screen-share): stop   -> $STOP"
