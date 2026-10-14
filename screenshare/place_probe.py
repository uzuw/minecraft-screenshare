#!/usr/bin/env python3
"""Close the pause menu, place a screen, and screenshot each step.

Mirrors layout_probe.py's approach: the game is XWayland (this GLFW has no Wayland
backend) so keys go through ydotool/uinput, and the mod polls key state per client tick,
so every press is HELD ~0.25 s rather than sent as an instant press/release.
"""
import json
import os
import subprocess
import time

# Derived from this file's own location: the probes are used from whatever checkout they live in.
SS = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(SS, "preview")
LOG = os.path.join(SS, "client.log")
KEY = {"ESC": 1, "F7": 65, "F8": 66, "N": 49, "F3": 61}


def game_window():
    clients = json.loads(subprocess.run(["hyprctl", "clients", "-j"],
                                        capture_output=True).stdout or b"[]")
    for c in clients:
        if "minecraft" in (c.get("class") or "").lower():
            return c
    return None


def press(*keys, settle=1.2):
    for k in keys:
        subprocess.run(["ydotool", "key", f"{KEY[k]}:1"], capture_output=True)
        time.sleep(0.25)
        subprocess.run(["ydotool", "key", f"{KEY[k]}:0"], capture_output=True)
        time.sleep(0.2)
    time.sleep(settle)


def shot(name):
    w = game_window()
    if w is None:
        print(f"  !! no game window for {name}")
        return
    x, y = w["at"]
    cx, cy = w["size"]
    path = os.path.join(OUT, name)
    subprocess.run(["grim", "-g", f"{x},{y} {cx}x{cy}", path], capture_output=True)
    print(f"  shot {name} ({os.path.getsize(path) if os.path.isfile(path) else 0} bytes)")


def log_tail(n=6):
    try:
        lines = [l for l in open(LOG, errors="replace").read().splitlines()
                 if "[screenshare]" in l]
    except OSError:
        return
    for l in lines[-n:]:
        print("     " + l.split("]: ", 1)[-1])


def main():
    os.makedirs(OUT, exist_ok=True)
    w = game_window()
    if w is None:
        print("no Minecraft window")
        return 1
    print(f"window {w['size']} at {w['at']}")

    # focus: input:follow_mouse is on, so parking the pointer over the window focuses it
    x, y = w["at"]
    cx, cy = w["size"]
    subprocess.run(["ydotool", "mousemove", "--absolute", str(x + cx // 2), str(y + cy // 2)],
                   capture_output=True)
    time.sleep(1.0)

    print("1. ESC - close the pause menu, back into the world")
    press("ESC", settle=1.5)
    shot("2-world.png")
    log_tail(3)

    print("2. F8 - edit mode is currently ON, so this turns it off")
    press("F8", settle=1.0)
    log_tail(2)

    print("3. F7 - place a screen where the player looks (binds next free source)")
    press("F7", settle=1.5)
    shot("3-placed.png")
    log_tail(4)

    print("4. step back for a wider view, then final shot")
    press("F8", settle=1.0)
    shot("4-final.png")
    log_tail(3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
