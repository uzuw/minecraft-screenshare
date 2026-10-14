#!/usr/bin/env python3
"""Screen-share capture service: publishes live monitor/window frames for Minecraft to render.

Why this design
---------------
* The compositor is Hyprland (Wayland). With no xdg-desktop-portal installed, the PipeWire
  ScreenCast route is unavailable, so capture goes through wlr-screencopy via `grim`.
* Measured on this host: `grim -t ppm` (raw, no PNG decode) is the fast path - ~37 ms capture
  for 1920x1080, versus ~187 ms for PNG. PIL decodes PPM in ~3 ms and a BOX downscale to the
  panel size costs ~12 ms, giving ~20 fps per source; each source runs in its own thread/process
  pair so N sources scale across cores.
* Do NOT try to make capture cheaper with grim's `-s` scale factor. Measured on this host:
  `-s 0.3333` on a 1080p output costs 111 ms/frame against 37 ms unscaled - three times SLOWER -
  because asking for a scaled buffer makes the compositor do a scaled render pass instead of
  handing over what it already has. It writes 9x less data and takes 3x longer. When the frames
  are too expensive, make the *output* smaller (see shrink_output in app_screens.sh): capturing a
  1280x720 output is 21 ms/frame, so a channel's own screen is nearly half price for free.
* What each source actually costs, so you can choose: 1080p monitor 37 ms/frame (the real desktop
  is the expensive one), 720p virtual output 21 ms/frame, region-of-window ~12 ms. At 12 fps that
  is ~44% of one core for a full-screen desktop feed and ~25% for a channel. This is why the
  service is started with `nice -n 10` and grim itself with `nice -n 15`: it must always lose the
  CPU fight to the compositor and the game.
* Frames are published to /dev/shm so the game reads memory, not disk.

Usage
-----
    capture_service.py --list                  # enumerate sources and exit
    capture_service.py --once --preview out/   # single frame per source + PNG previews
    capture_service.py --fps 12                # run as a daemon (Ctrl-C to stop)
    capture_service.py --sources windows       # one screen per APPLICATION window
    capture_service.py --sources monitors      # one screen per monitor
    capture_service.py --sources all           # both (default)

Application windows
-------------------
`--sources windows` publishes one source per application window, keyed by app class:
`win-google-chrome`, `win-spotify`, and `win-google-chrome-2` for a second window of the
same app. The id is deliberately *not* the window address, which changes on every launch
and would orphan a panel already placed in the world. The manifest entry also carries
`app` and `title` so the in-game source list is readable.

The worker re-reads each window's rectangle twice a second, so a window that is moved or
resized keeps being captured correctly instead of lagging behind until the next source scan.

Caveat on window sources: region capture reads what the compositor shows at that rectangle,
so an *occluded* window yields whatever is on top of it. Monitor sources have no such issue.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import frame_format as ff  # noqa: E402

GRIM = shutil.which("grim")
# Capture must never outrank the game or the compositor for CPU. On this host the desktop is
# already memory-tight, and a grim that takes a whole core while Minecraft is initialising is
# felt as stutter in every window on screen. nice(1) hands the CPU back automatically.
NICE = ["nice", "-n", "15"] if shutil.which("nice") else []
HYPRCTL = shutil.which("hyprctl")

# A screen source per *application*, not per window instance: a window's address changes
# every time the app launches, so an id built from it would orphan a placed panel. We key
# on the app class instead and keep the address only as a live handle for geometry.
SELF_CLASS_HINTS = ("minecraft",)   # never mirror the game into itself
MIN_WINDOW_PX = 200                 # smaller than this is a dialog/tooltip, not a screen
GEOMETRY_REFRESH_S = 0.5            # windows get moved/resized; re-read their rect this often


def slug(text: str) -> str:
    """A stable, filesystem-safe id fragment: 'google-chrome' stays, spaces collapse."""
    out = []
    for ch in (text or "").strip().lower():
        if ch.isalnum() or ch in "-_.":
            out.append(ch)
        elif ch in " /":
            out.append("-")
    return "".join(out).strip("-")[:32] or "window"


def window_capture_args(address: str) -> list[str] | None:
    """Current `grim -g` rectangle for a window, or None when it no longer exists."""
    if not HYPRCTL:
        return None
    try:
        out = subprocess.run([HYPRCTL, "clients", "-j"], capture_output=True, timeout=5)
        clients = json.loads(out.stdout or b"[]")
    except Exception:
        return None
    for c in clients:
        if str(c.get("address", "")).replace("0x", "") != address.replace("0x", ""):
            continue
        if not c.get("mapped"):
            return None
        x, y = c.get("at", [0, 0])
        w, h = c.get("size", [0, 0])
        if w <= 0 or h <= 0:
            return None
        return ["-g", f"{x},{y} {w}x{h}"]
    return None



# --------------------------------------------------------------------------- sources

def channel_names() -> dict[str, str]:
    """output name -> channel name, from channels.json (written by app_screens.sh).

    Hyprland hands out a different name for a headless output after every restart
    (HEADLESS-8 becomes HEADLESS-5, and so on), so a source id built from the output name
    moves out from under a panel already placed in the world. app_screens.sh records which
    output belongs to which channel, and we publish the channel name instead, which is
    stable across restarts.
    """
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "channels.json")) as fh:
            data = json.load(fh)
    except Exception:
        return {}
    mapping = {}
    for name, entry in (data or {}).items():
        output = (entry or {}).get("output")
        if output:
            mapping[str(output)] = str(name)
    return mapping


def list_monitors() -> list[dict]:
    if not HYPRCTL:
        return []
    try:
        out = subprocess.run([HYPRCTL, "monitors", "-j"], capture_output=True, timeout=10)
        monitors = json.loads(out.stdout or b"[]")
    except Exception:
        return []
    channels = channel_names()
    used: set[str] = set()
    sources = []
    for m in monitors:
        name = m.get("name", "unknown")
        channel = channels.get(name)
        source_id = f"mon-{slug(channel)}" if channel else f"mon-{name}"
        if source_id in used:      # two outputs claiming one channel: fall back to the real name
            source_id = f"mon-{name}"
        used.add(source_id)
        source = {
            "id": source_id,
            "kind": "monitor",
            "output": name,
            "name": channel or name,
            "detail": f"{m.get('width')}x{m.get('height')}@{m.get('refreshRate')} at {m.get('x')},{m.get('y')}",
            "native_w": int(m.get("width") or 0),
            "native_h": int(m.get("height") or 0),
            "capture": ["-o", name],
            "focused": bool(m.get("focused")),
        }
        if channel:
            source["channel"] = channel
        sources.append(source)
    return sources



def list_windows() -> list[dict]:
    """One screen source per application window.

    Ids are derived from the app class (`win-google-chrome`), so a panel placed on
    "the Chrome screen" survives relaunching Chrome. Several windows of the same class
    are disambiguated with a numeric suffix. The address is kept as a live handle so the
    worker can re-read the geometry as the window is moved or resized.
    """
    if not HYPRCTL:
        return []
    try:
        out = subprocess.run([HYPRCTL, "clients", "-j"], capture_output=True, timeout=10)
        clients = json.loads(out.stdout or b"[]")
    except Exception:
        return []
    sources: list[dict] = []
    seen: dict[str, int] = {}
    for c in clients:
        if not c.get("mapped"):
            continue
        w, h = c.get("size", [0, 0])
        if w < MIN_WINDOW_PX or h < MIN_WINDOW_PX:
            continue  # dialogs, tooltips, slivers - not something you'd put on a panel
        klass = (c.get("class") or "").strip()
        title = (c.get("title") or "").strip()
        if any(hint in klass.lower() for hint in SELF_CLASS_HINTS):
            continue  # the game itself: would be an infinite hall of mirrors
        base = slug(klass or title)
        seen[base] = seen.get(base, 0) + 1
        # `win-chrome` for the first, `win-chrome-2` for a second window of the same app
        source_id = f"win-{base}" if seen[base] == 1 else f"win-{base}-{seen[base]}"
        x, y = c.get("at", [0, 0])
        address = str(c.get("address", "")).replace("0x", "")
        sources.append({
            "id": source_id,
            "kind": "window",
            "app": klass or "(unknown)",
            "title": title[:80],
            "name": klass or "window",
            "detail": f"{title[:52]} - {w}x{h} at {x},{y}",
            "native_w": int(w),
            "native_h": int(h),
            "address": address,
            "capture": ["-g", f"{x},{y} {w}x{h}"],
            "occlusion": "region capture: shows whatever is on top at that rectangle",
        })
    return sources



# --------------------------------------------------------------------------- capture

def grab(source: dict, cursor: bool) -> Image.Image | None:
    """Capture one frame from a source as a PIL image (PPM = no decode cost)."""
    args = [*NICE, GRIM, *source["capture"], "-t", "ppm"]
    if cursor:
        args.append("-c")
    args.append("-")
    try:
        proc = subprocess.run(args, capture_output=True, timeout=20)
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    try:
        img = Image.open(io.BytesIO(proc.stdout))
        img.load()
        return img
    except Exception:
        return None


def downscale(img: Image.Image, width: int, height: int) -> Image.Image:
    """Fast downscale: integer BOX reduce when possible, else BOX resize."""
    img = img.convert("RGB")
    if img.width == width and img.height == height:
        return img
    ratio = img.width / width
    if abs(ratio - round(ratio)) < 0.01 and ratio >= 2 and abs(img.height / height - round(ratio)) < 0.01:
        return img.reduce(int(round(ratio)))
    return img.resize((width, height), Image.BOX)


class SourceWorker(threading.Thread):
    """Captures one source at a target rate and publishes frames to the shared dir."""

    def __init__(self, source: dict, out_w: int, out_h: int, fps: float, directory: str,
                 cursor: bool, quiet: bool):
        super().__init__(daemon=True, name=source["id"])
        self.source = source
        self.out_w, self.out_h = out_w, out_h
        self.interval = 1.0 / fps if fps > 0 else 0.0
        self.dir = directory
        self.cursor = cursor
        self.quiet = quiet
        self.stop_event = threading.Event()
        self.seq = 0
        self.fps_actual = 0.0
        self.last_ts = 0
        self.errors = 0
        self.alive = False
        self.last_native = (source["native_w"], source["native_h"])
        self.address = str(source.get("address") or "") or None
        self.geom_refresh_at = 0.0

    def run(self) -> None:
        next_at = time.time()
        window: list[float] = []
        while not self.stop_event.is_set():
            started = time.time()
            # A window gets moved and resized constantly; re-read its rectangle so the
            # capture region follows it instead of lagging until the next 5 s source scan.
            if self.address and time.time() >= self.geom_refresh_at:
                self.geom_refresh_at = time.time() + GEOMETRY_REFRESH_S
                args = window_capture_args(self.address)
                if args is None:
                    self.alive = False
                    break  # closed or unmapped; the daemon drops it on its next sync
                self.source["capture"] = args
            img = grab(self.source, self.cursor)
            if img is None:
                self.errors += 1
                self.alive = False
                time.sleep(min(1.0, max(self.interval, 0.25)))
                continue
            self.last_native = (img.width, img.height)
            small = downscale(img, self.out_w, self.out_h)
            pixels = small.tobytes()
            self.seq += 1
            ts = int(time.time() * 1000)
            try:
                ff.publish(self.dir, self.source["id"], ff.pack_frame(
                    small.width, small.height, self.seq, pixels, ts_ms=ts))
                self.last_ts = ts
                self.alive = True
            except OSError as exc:
                self.errors += 1
                if not self.quiet:
                    print(f"[{self.source['id']}] publish failed: {exc}", flush=True)
            window.append(started)
            cutoff = started - 2.0
            while window and window[0] < cutoff:
                window.pop(0)
            span = (window[-1] - window[0]) if len(window) > 1 else 1.0
            self.fps_actual = (len(window) - 1) / span if span > 0 else 0.0
            next_at += self.interval
            sleep_for = next_at - time.time()
            if sleep_for > 0:
                self.stop_event.wait(sleep_for)
            else:
                next_at = time.time()  # fell behind: resync rather than spin

    def manifest_entry(self) -> dict:
        entry = {
            "id": self.source["id"],
            "kind": self.source["kind"],
            "name": self.source["name"],
            "detail": self.source["detail"],
            "native_w": self.last_native[0],
            "native_h": self.last_native[1],
            "out_w": self.out_w,
            "out_h": self.out_h,
            "target_fps": round(1.0 / self.interval, 1) if self.interval else 0,
            "fps": round(self.fps_actual, 1),
            "last_seq": self.seq,
            "last_ts_ms": self.last_ts,
            "alive": self.alive,
            "errors": self.errors,
        }
        for key in ("focused", "occlusion", "app", "title", "channel"):
            if key in self.source:
                entry[key] = self.source[key]
        return entry


# --------------------------------------------------------------------------- modes

def cmd_list(sources: list[dict]) -> int:
    if not sources:
        print("no sources found (is grim/hyprctl available and are you in a Hyprland session?)")
        return 1
    for s in sources:
        print(f"{s['id']:<24} {s['kind']:<8} {s['name']:<14} {s['detail']}")
    return 0


def cmd_once(sources: list[dict], args) -> int:
    os.makedirs(args.dir, exist_ok=True)
    if args.preview:
        os.makedirs(args.preview, exist_ok=True)
    rc = 0
    for s in sources:
        t0 = time.time()
        img = grab(s, args.cursor)
        if img is None:
            print(f"{s['id']:<24} CAPTURE FAILED")
            rc = 1
            continue
        small = downscale(img, args.width, args.height)
        ff.publish(args.dir, s["id"], ff.pack_frame(small.width, small.height, 1,
                                                   small.tobytes()))
        took = (time.time() - t0) * 1000
        print(f"{s['id']:<24} {img.width}x{img.height} -> {small.width}x{small.height} "
              f"in {took:.0f} ms")
        if args.preview:
            path = os.path.join(args.preview, f"{s['id']}.png")
            small.save(path)
            print(f"{'':<24} preview: {path}")
    return rc


def cmd_daemon(sources: list[dict], args) -> int:
    """Publish every source, re-scanning for displays/windows as they come and go."""
    os.makedirs(args.dir, exist_ok=True)
    workers: dict[str, SourceWorker] = {}

    def wanted() -> dict[str, dict]:
        found: list[dict] = []
        if args.sources in ("monitors", "all"):
            found += list_monitors()
        if args.sources in ("windows", "all"):
            found += list_windows()
        return {s["id"]: s for s in found}

    def sync() -> None:
        want = wanted()
        for sid, source in want.items():
            if sid not in workers:
                worker = SourceWorker(source, args.width, args.height, args.fps, args.dir,
                                      args.cursor, args.quiet)
                workers[sid] = worker
                worker.start()
                if not args.quiet:
                    print(f"  + {sid:<22} {source['detail']}", flush=True)
        for sid in list(workers):
            if sid not in want:
                worker = workers.pop(sid)
                worker.stop_event.set()
                worker.join(timeout=3)
                try:
                    os.remove(ff.frame_path(args.dir, sid))
                except OSError:
                    pass
                if not args.quiet:
                    print(f"  - {sid} (gone)", flush=True)

    sync()
    if not workers:
        print("no sources available", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"capture service: {len(workers)} source(s) -> {args.dir} "
              f"at {args.width}x{args.height} target {args.fps} fps "
              f"(re-scanning every {args.rescan} s)", flush=True)
    next_sync = time.time() + args.rescan
    try:
        while True:
            time.sleep(1.0)
            if args.rescan > 0 and time.time() >= next_sync:
                next_sync = time.time() + args.rescan
                sync()
            ff.write_manifest(args.dir, [w.manifest_entry() for w in workers.values()])
            if not args.quiet:
                parts = [f"{w.source['id']}={w.fps_actual:.1f}fps" + ("" if w.alive else " DEAD")
                         for w in workers.values()]
                print("  " + "  ".join(parts), flush=True)
    except KeyboardInterrupt:
        for worker in workers.values():
            worker.stop_event.set()
        for worker in workers.values():
            worker.join(timeout=3)
        if not args.quiet:
            print("\nstopped")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="publish live screen frames for Minecraft")
    ap.add_argument("--list", action="store_true", help="enumerate sources and exit")
    ap.add_argument("--once", action="store_true", help="capture one frame per source and exit")
    ap.add_argument("--preview", metavar="DIR", help="with --once: also write PNG previews")
    ap.add_argument("--fps", type=float, default=12.0,
                    help="per-source capture rate (12 keeps the desktop smooth on a busy "
                         "machine; 15+ is possible but competes with the game for CPU)")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=360)
    ap.add_argument("--dir", default=ff.DEFAULT_DIR)
    ap.add_argument("--cursor", action="store_true", help="include the cursor")
    ap.add_argument("--sources", choices=["monitors", "windows", "all"], default="all")
    ap.add_argument("--rescan", type=float, default=5.0,
                    help="seconds between source re-scans, 0 disables hotplug detection")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if not GRIM:
        print("grim not found - cannot capture on this compositor", file=sys.stderr)
        return 2

    sources: list[dict] = []
    if args.sources in ("monitors", "all"):
        sources += list_monitors()
    if args.sources in ("windows", "all"):
        sources += list_windows()

    if args.list:
        return cmd_list(sources)
    if not sources:
        print("no sources available", file=sys.stderr)
        return 1
    if args.once:
        return cmd_once(sources, args)
    return cmd_daemon(sources, args)


if __name__ == "__main__":
    sys.exit(main())
