#!/usr/bin/env python3
"""Drive the in-game layout controls and capture screenshots as evidence.

The game runs through XWayland (this build's GLFW has no Wayland backend), and XWayland
windows never receive Wayland virtual-keyboard events, so keys are injected with ydotool
(uinput level, reaches X11 and Wayland clients alike) - wtype does not work here.

Focuses the Minecraft window, presses F6 (layout mode), resizes and moves the selected
panel, then leaves layout mode - capturing the game window at each step so the result can
be checked visually instead of trusted from the log.

Usage: layout_probe.py <output-dir>
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time


# linux/input-event-codes.h values, which is what ydotool speaks
KEYCODES = {
    "F6": 64,
    "equal": 13,
    "minus": 12,
    "Tab": 15,
    "Right": 106,
    "Left": 105,
    "Up": 103,
    "Down": 108,
    "Delete": 111,
    "r": 19,
}


def game_window() -> dict | None:
    out = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True)
    try:
        clients = json.loads(out.stdout or b"[]")
    except json.JSONDecodeError:
        return None
    for client in clients:
        if "minecraft" in (client.get("class") or "").lower():
            return client
    return None


def focus(window: dict) -> None:
    """Give the game keyboard focus by moving the pointer into it.

    `hyprctl dispatch focuswindow` is broken on this Hyprland build (its Lua dispatch parser
    rejects `focuswindow address:0x...`), but input:follow_mouse is on, so parking the pointer
    over the window focuses it - which is what actually gets keys delivered to the client.
    """
    x, y = window["at"]
    w, h = window["size"]
    subprocess.run(["ydotool", "mousemove", "--absolute", str(x + w // 2), str(y + h // 2)],
                   capture_output=True)
    time.sleep(1.0)


def press(*keys: str, settle: float = 0.6) -> None:
    """Press and release each key with a gap in between.

    The game reads key state by polling (InputConstants.isKeyDown, once per client tick), so
    a press and release issued in the same instant - which is what `ydotool key c:1 c:0` does -
    is invisible to it. Hold each key long enough for at least one tick to observe it.
    """
    for key in keys:
        code = KEYCODES[key]
        down = subprocess.run(["ydotool", "key", f"{code}:1"], capture_output=True)
        time.sleep(0.25)
        up = subprocess.run(["ydotool", "key", f"{code}:0"], capture_output=True)
        if down.returncode != 0 or up.returncode != 0:
            print(f"  !! ydotool failed for {key}: "
                  f"{(down.stderr or up.stderr).decode().strip()}")
        time.sleep(0.15)
    time.sleep(settle)


def shot(window: dict, path: str) -> bool:
    x, y = window["at"]
    w, h = window["size"]
    subprocess.run(["grim", "-g", f"{x},{y} {w}x{h}", path], capture_output=True)
    ok = os.path.isfile(path) and os.path.getsize(path) > 1000
    print(f"  {'captured' if ok else 'FAILED  '} {path}")
    return ok


def main() -> int:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out_dir, exist_ok=True)
    window = game_window()
    if window is None:
        print("no Minecraft window found", file=sys.stderr)
        return 1
    print(f"game window: {window['size']} at {window['at']}")
    focus(window)

    print("1. baseline (layout mode off)")
    shot(window, os.path.join(out_dir, "1-baseline.png"))

    print("2. F6 -> layout mode on (expect highlight + help text)")
    press("F6", settle=1.0)
    shot(window, os.path.join(out_dir, "2-layout-mode.png"))

    print("3. '+' x4 then Right/Right (expect a bigger panel, moved right)")
    press("equal", "equal", "equal", "equal", "Right", "Right", settle=1.0)
    shot(window, os.path.join(out_dir, "3-resized.png"))

    print("4. F6 -> leave layout mode (layout is saved)")
    press("F6", settle=1.5)
    shot(window, os.path.join(out_dir, "4-final.png"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
