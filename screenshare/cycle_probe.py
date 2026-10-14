#!/usr/bin/env python3
"""Cycle the placed screen's source with N until it is the YouTube channel, then shoot."""
import json
import os
import subprocess
import sys
import time

# Derived from this file's own location: the probes are used from whatever checkout they live in.
SS = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(SS, "preview")
LOG = os.path.join(SS, "client.log")
KEY = {"N": 49, "F8": 66, "F7": 65}


def game_window():
    clients = json.loads(subprocess.run(["hyprctl", "clients", "-j"],
                                        capture_output=True).stdout or b"[]")
    for c in clients:
        if "minecraft" in (c.get("class") or "").lower():
            return c
    return None


def press(*keys, settle=1.0):
    for k in keys:
        subprocess.run(["ydotool", "key", f"{KEY[k]}:1"], capture_output=True)
        time.sleep(0.25)
        subprocess.run(["ydotool", "key", f"{KEY[k]}:0"], capture_output=True)
        time.sleep(0.2)
    time.sleep(settle)


def shot(name):
    w = game_window()
    if w is None:
        print(f"  !! no game window, cannot shoot {name}")
        return
    x, y = w["at"]
    cx, cy = w["size"]
    path = os.path.join(OUT, name)
    subprocess.run(["grim", "-g", f"{x},{y} {cx}x{cy}", path], capture_output=True)
    print(f"  shot {name} ({os.path.getsize(path) if os.path.isfile(path) else 0} bytes)")


def last_edit():
    """The most recent 'edit <sourceId> ...' line the mod printed."""
    try:
        lines = [l for l in open(LOG, errors="replace").read().splitlines()
                 if "[screenshare]" in l]
    except OSError:
        return ""
    for l in reversed(lines):
        if "edit " in l:
            return l.split("edit ", 1)[1].split()[0]
    return ""


def main():
    w = game_window()
    if w is None:
        print("no Minecraft window")
        return 1
    x, y = w["at"]
    cx, cy = w["size"]
    subprocess.run(["ydotool", "mousemove", "--absolute", str(x + cx // 2), str(y + cy // 2)],
                   capture_output=True)
    time.sleep(1.0)

    print(f"currently editing: {last_edit() or '(nothing)'}")
    print("pressing N to cycle the source (up to 3 times)")
    for i in range(3):
        press("N", settle=1.0)
        cur = last_edit()
        print(f"  press {i + 1} -> {cur or '(no edit line)'}")
        if cur == "mon-HEADLESS-8":
            print("  *** the screen is now bound to the YouTube channel ***")
            break

    shot("5-cycled.png")
    print("\nleaving edit mode")
    press("F8", settle=1.5)
    shot("6-final.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
