#!/usr/bin/env python3
"""Mirror an already-open application window into Minecraft, via xdg-desktop-portal.

Why this route (measured 2026-10-14; see ../notes/CHANNELS.md)
-------------------------------------------------------------
A portal **Window** source captures the toplevel's *own buffer*:

  * occlusion-proof - unlike our `window` kind, which is `grim -g <rect>` region capture
    and therefore returns whatever the compositor has on top of that rectangle;
  * damage-driven - PipeWire only delivers buffers when the window changes, so a static
    window costs ~0, whereas grim re-reads the whole frame every tick regardless.

The portal hands us a PipeWire stream; `pipewiresrc` + `appsink` turns that into raw RGB
frames, which we publish in the same MCSF format the game already reads. **The Java side
needs no changes** - it enumerates sources.json, and this module contributes through
frame_format.write_manifest_part() so it never fights the capture service over that file.

Requires the SYSTEM python: the GObject/GStreamer bindings live in `/usr/bin/python3`
(Hermes's bundled python has no `gi`), and so does PIL.

Usage
-----
    /usr/bin/python3 portal_capture.py --setup    # pick windows once (INTERACTIVE)
    /usr/bin/python3 portal_capture.py --list     # show what the saved token grants
    /usr/bin/python3 portal_capture.py --run      # publish frames for the game

`--setup` shows the portal's window chooser; select the windows you want on a panel.
The portal then returns a **restore_token**, which is saved so every later run is
non-interactive.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

try:
    import gi
    gi.require_version("Gst", "1.0")
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib, Gst
except (ImportError, ValueError) as exc:  # wrong interpreter is the usual cause
    sys.exit(f"this needs the system python with gi/Gst:\n"
             f"  /usr/bin/python3 {os.path.basename(__file__)} ...\n"
             f"({type(exc).__name__}: {exc})")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import frame_format as ff  # noqa: E402

PORTAL_DEST = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"

SOURCE_WINDOW = 2          # ScreenCast source type bitmask: 1=Monitor 2=Window 4=Virtual
CURSOR_EMBEDDED = 2        # 1=Hidden 2=Embedded
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portal_tokens.json")

RESPONSE_SUCCESS, RESPONSE_CANCELLED, RESPONSE_OTHER = 0, 1, 2


# --------------------------------------------------------------------------- portal session

class PortalScreenCast:
    """The D-Bus half: create a session, select Window sources, start it."""

    def __init__(self) -> None:
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        # The portal derives request/session object paths from our unique bus name.
        self.sender_token = self.bus.get_unique_name()[1:].replace(".", "_")
        self.session_handle: str | None = None
        self._counter = 0

    # -- request plumbing ---------------------------------------------------

    def _token(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_{os.getpid()}_{self._counter}_{int(time.time() * 1000) % 100000}"

    def _response(self, handle_token: str, iface: str, method: str,
                  params: GLib.Variant, timeout: float = 180.0) -> dict:
        """Call a portal method and wait for its Response signal.

        The request object path is predictable from the handle_token we pass, so we
        subscribe *before* calling - the response can otherwise arrive first and be missed.
        """
        expected = f"{PORTAL_PATH}/request/{self.sender_token}/{handle_token}"
        state: dict = {}
        loop = GLib.MainLoop()

        def on_response(_conn, _sender, _path, _iface, _signal, args) -> None:
            state["response"], state["results"] = args[0], args[1]
            loop.quit()

        sub = self.bus.signal_subscribe(PORTAL_DEST, REQUEST_IFACE, "Response",
                                        expected, None, Gio.DBusSignalFlags.NONE, on_response)

        def on_timeout() -> bool:
            state["timeout"] = True
            loop.quit()
            return False

        timer = GLib.timeout_add_seconds(int(timeout), on_timeout)
        try:
            reply = self.bus.call_sync(PORTAL_DEST, PORTAL_PATH, iface, method, params,
                                       GLib.VariantType.new("(o)"),
                                       Gio.DBusCallFlags.NONE, 5000, None)
            actual = reply.unpack()[0]
            if actual != expected:
                # We did not get to choose the path; resubscribe to the real one.
                self.bus.signal_unsubscribe(sub)
                sub = self.bus.signal_subscribe(PORTAL_DEST, REQUEST_IFACE, "Response",
                                                actual, None, Gio.DBusSignalFlags.NONE,
                                                on_response)
            loop.run()
        finally:
            GLib.source_remove(timer)
            self.bus.signal_unsubscribe(sub)

        if state.get("timeout"):
            raise TimeoutError(f"{method} produced no Response within {timeout:.0f}s")
        code = state.get("response", RESPONSE_OTHER)
        if code == RESPONSE_CANCELLED:
            raise RuntimeError("cancelled at the portal chooser")
        if code != RESPONSE_SUCCESS:
            raise RuntimeError(f"{method} failed (response code {code})")
        return state.get("results") or {}

    # -- the handshake ------------------------------------------------------

    def create_session(self) -> str:
        handle = self._token("cs")
        results = self._response(handle, SCREENCAST_IFACE, "CreateSession",
                                 GLib.Variant("(a{sv})", ({
                                     "handle_token": GLib.Variant("s", handle),
                                     "session_handle_token": GLib.Variant("s", self._token("ss")),
                                 },)))
        self.session_handle = results["session_handle"]
        assert isinstance(self.session_handle, str)
        return self.session_handle

    def select_windows(self, restore_token: str | None, interactive: bool) -> None:
        """Ask for Window sources.

        With a valid restore_token and interactive=False the portal restores the previous
        selection silently. Without one it pops its chooser, which is the interactive step.
        """
        handle = self._token("sel")
        options: dict[str, GLib.Variant] = {
            "handle_token": GLib.Variant("s", handle),
            "types": GLib.Variant("u", SOURCE_WINDOW),
            "multiple": GLib.Variant("b", True),
            "cursor_mode": GLib.Variant("u", CURSOR_EMBEDDED),
        }
        if restore_token:
            options["restore_token"] = GLib.Variant("s", restore_token)
        if interactive:
            print("  >>> a window chooser should now appear on your screen -"
                  " pick the window(s) you want on a panel <<<", flush=True)
        self._response(handle, SCREENCAST_IFACE, "SelectSources",
                       GLib.Variant("(oa{sv})", (self.session_handle, options)))

    def start(self) -> tuple[list[tuple[int, dict]], str | None]:
        handle = self._token("start")
        results = self._response(handle, SCREENCAST_IFACE, "Start",
                                 GLib.Variant("(osa{sv})", (self.session_handle, "", {
                                     "handle_token": GLib.Variant("s", handle),
                                 },)))
        streams = []
        for node_id, props in results.get("streams", []):
            streams.append((int(node_id), {k: v for k, v in props.items()}))
        return streams, results.get("restore_token")

    def close(self) -> None:
        if not self.session_handle:
            return
        try:
            self.bus.call_sync(PORTAL_DEST, self.session_handle, SESSION_IFACE, "Close",
                               None, None, Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error:
            pass
        self.session_handle = None


# --------------------------------------------------------------------------- token store

def save_state(streams: list[tuple[int, dict]], restore_token: str | None) -> None:
    payload = {
        "updated_ms": int(time.time() * 1000),
        "restore_token": restore_token,
        "streams": [{"node_id": node, "props": props} for node, props in streams],
    }
    tmp = f"{TOKEN_FILE}.tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp, TOKEN_FILE)
    os.chmod(TOKEN_FILE, 0o600)


def load_state() -> dict | None:
    try:
        with open(TOKEN_FILE) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------- named windows

SOURCE_TYPE_NAMES = {1: "Monitor", 2: "Window", 3: "Monitor", 4: "Virtual"}


def grant_kind(props: dict) -> tuple[str, str]:
    """What did the portal ACTUALLY grant? Returns (kind, evidence).

    This exists because it cost real time: asking for Window sources on this host silently
    produced a Monitor grant, the log said "got 1 stream" and the script said a token was
    saved, so the result looked like a success. A granted monitor has the monitor's exact
    resolution, which is easy to mistake for a maximised window.
    """
    st = props.get("source_type")
    if st is not None:
        try:
            return SOURCE_TYPE_NAMES.get(int(st), f"type {int(st)}"), "source_type"
        except (TypeError, ValueError):
            pass
    if props.get("mapping_id"):
        # xdg-desktop-portal-hyprland names the output here for monitor grants
        return "Monitor", f"mapping_id={props['mapping_id']}"
    size = props.get("size")
    if size and len(size) == 2:
        try:
            import subprocess
            mons = json.loads(subprocess.run(["hyprctl", "monitors", "-j"],
                                             capture_output=True, timeout=5).stdout or b"[]")
            for m in mons:
                if (int(size[0]), int(size[1])) == (m["width"], m["height"]):
                    return "Monitor", f"size matches display {m['name']}"
        except Exception:
            pass
    return "Window", "assumed"


def describe_window(props: dict) -> str:
    """Best-effort label for a stream, so the in-game source list is readable.

    The portal reports geometry, not a title, so we match it against the compositor's
    live client list. Purely cosmetic: a miss just yields a generic name.
    """
    size = props.get("size")
    if not size or len(size) != 2:
        return "window"
    w, h = int(size[0]), int(size[1])
    try:
        import subprocess
        out = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, timeout=5)
        clients = json.loads(out.stdout or b"[]")
    except Exception:
        return f"{w}x{h}"
    for c in clients:
        cw, ch = c.get("size", [0, 0])
        if abs(int(cw) - w) <= 2 and abs(int(ch) - h) <= 2:
            klass = (c.get("class") or "").strip()
            title = (c.get("title") or "").strip()
            if klass:
                return f"{klass}: {title[:28]}" if title else klass
    return f"{w}x{h}"


# --------------------------------------------------------------------------- frame producer

class PortalStream(threading.Thread):
    """Consumes one PipeWire stream node and publishes MCSF frames like any other source."""

    def __init__(self, index: int, node_id: int, props: dict, out_w: int, out_h: int,
                 directory: str, fps_cap: float, quiet: bool):
        super().__init__(daemon=True, name=f"portal-{index}")
        self.source_id = f"portal-{index}"
        self.node_id = node_id
        self.props = props
        self.out_w, self.out_h = out_w, out_h
        self.dir = directory
        self.interval = 1.0 / fps_cap if fps_cap > 0 else 0.0
        self.quiet = quiet
        self.stop_event = threading.Event()
        self.seq = 0
        self.fps_actual = 0.0
        self.last_ts = 0
        self.errors = 0
        self.alive = False
        self.native_w, self.native_h = (props.get("size") or [out_w, out_h])[:2]
        self.label = describe_window(props)
        self.pipeline = None

    def build_pipeline(self):
        """pipewiresrc -> RGB at our published size. Damage-driven: idle windows cost ~0.

        The caps filter pins width/height/format so videoscale does the work on the GPU
        side of the graph and appsink hands us exactly the bytes we publish.
        """
        desc = (
            f"pipewiresrc path={self.node_id} do-timestamp=true "
            f"! videoconvert "
            f"! videoscale "
            f"! video/x-raw,format=RGB,width={self.out_w},height={self.out_h} "
            f"! appsink name=sink max-buffers=2 drop=true sync=false emit-signals=false"
        )
        pipeline = Gst.parse_launch(desc)
        self.pipeline = pipeline
        return pipeline

    def run(self) -> None:
        try:
            pipeline = self.build_pipeline()
        except GLib.Error as exc:
            self.errors += 1
            if not self.quiet:
                print(f"[{self.source_id}] pipeline failed: {exc}", flush=True)
            return
        sink = pipeline.get_by_name("sink")
        pipeline.set_state(Gst.State.PLAYING)
        window: list[float] = []
        next_at = time.time()
        try:
            while not self.stop_event.is_set():
                # 200 ms of patience: a damage-driven stream is legitimately silent while
                # the window is unchanged, and we must not mistake that for a failure.
                sample = sink.emit("try-pull-sample", 200_000_000)
                if sample is None:
                    ok, state, _ = pipeline.get_state(0)
                    if state in (Gst.State.NULL, Gst.State.READY) and self.seq > 0:
                        self.alive = False
                        break
                    continue
                buf = sample.get_buffer()
                ok, info = buf.map(Gst.MapFlags.READ)
                if not ok:
                    self.errors += 1
                    continue
                try:
                    pixels = bytes(info.data)
                finally:
                    buf.unmap(info)
                expected = self.out_w * self.out_h * 3
                if len(pixels) != expected:
                    self.errors += 1
                    continue
                self.native_w, self.native_h = (sample.get_caps().get_structure(0)
                                                .get_value("width"),
                                                sample.get_caps().get_structure(0)
                                                .get_value("height"))
                self.seq += 1
                ts = int(time.time() * 1000)
                try:
                    ff.publish(self.dir, self.source_id,
                               ff.pack_frame(self.out_w, self.out_h, self.seq, pixels, ts_ms=ts))
                    self.last_ts = ts
                    self.alive = True
                except OSError as exc:
                    self.errors += 1
                    if not self.quiet:
                        print(f"[{self.source_id}] publish failed: {exc}", flush=True)
                now = time.time()
                window.append(now)
                cutoff = now - 2.0
                while window and window[0] < cutoff:
                    window.pop(0)
                span = (window[-1] - window[0]) if len(window) > 1 else 1.0
                self.fps_actual = (len(window) - 1) / span if span > 0 else 0.0
                if self.interval:  # respect the cap without spinning
                    next_at += self.interval
                    sleep_for = next_at - time.time()
                    if sleep_for > 0:
                        self.stop_event.wait(sleep_for)
                    else:
                        next_at = time.time()
        finally:
            pipeline.set_state(Gst.State.NULL)

    def manifest_entry(self) -> dict:
        return {
            "id": self.source_id,
            "kind": "portal",
            "name": self.label,
            "detail": f"mirrored window {self.native_w}x{self.native_h} "
                      f"(node {self.node_id}, damage-driven)",
            "native_w": int(self.native_w), "native_h": int(self.native_h),
            "out_w": self.out_w, "out_h": self.out_h,
            "target_fps": round(1.0 / self.interval, 1) if self.interval else 0,
            "fps": round(self.fps_actual, 1),
            "last_seq": self.seq, "last_ts_ms": self.last_ts,
            "alive": self.alive, "errors": self.errors,
        }


# --------------------------------------------------------------------------- modes

def open_session(interactive: bool) -> tuple[list[tuple[int, dict]], str | None]:
    portal = PortalScreenCast()
    portal.create_session()
    state = load_state() or {}
    token = None if interactive else state.get("restore_token")
    if token:
        print(f"  restoring previous selection (token {token[:12]}...)", flush=True)
    portal.select_windows(token, interactive=interactive or not token)
    streams, new_token = portal.start()
    if not streams:
        raise SystemExit("the portal granted no streams")
    save_state(streams, new_token or token)
    return streams, (new_token or token)


def cmd_setup(_args) -> int:
    print("creating a ScreenCast session and asking for Window sources...")
    streams, token = open_session(interactive=True)
    print(f"\ngot {len(streams)} stream(s):")
    kinds = []
    for i, (node, props) in enumerate(streams):
        kind, why = grant_kind(props)
        kinds.append(kind)
        print(f"  portal-{i}  node={node}  {kind:<8} {describe_window(props)}  "
              f"{props.get('size')}  ({why})")
    if "Window" not in kinds and kinds:
        print(f"\n  WARNING: asked for Window sources but the portal granted {', '.join(kinds)}.")
        print("  The portal chose silently - there was no picker to accept, and the stream is")
        print("  the whole display, not a window. Use app_screens.sh for a real per-application")
        print("  screen instead. See notes/CHANNELS.md.")
    if token:
        print(f"\nrestore_token saved to {TOKEN_FILE} -> later runs need no chooser")
    else:
        print(f"\nrestore_token: the portal returned NONE, so --setup will need to run again")
        print(f"  every time (state file {TOKEN_FILE} holds no token)")
    return 0


def cmd_list(_args) -> int:
    state = load_state()
    if not state:
        print(f"no saved token at {TOKEN_FILE} - run --setup first")
        return 1
    age = (time.time() * 1000 - state.get("updated_ms", 0)) / 1000
    print(f"token file   : {TOKEN_FILE}")
    print(f"restore_token: {(state.get('restore_token') or '')[:16]}...")
    print(f"updated      : {age:.0f}s ago")
    for i, s in enumerate(state.get("streams", [])):
        print(f"  portal-{i}  node={s.get('node_id')}  {describe_window(s.get('props') or {})}"
              f"  {s.get('props', {}).get('size')}")
    return 0


def cmd_run(args) -> int:
    state = load_state()
    if not state or not state.get("restore_token"):
        print("no usable restore_token - run --setup first", file=sys.stderr)
        return 1
    Gst.init(None)
    streams, _ = open_session(interactive=False)
    os.makedirs(args.dir, exist_ok=True)

    workers: list[PortalStream] = []
    for i, (node, props) in enumerate(streams):
        w = PortalStream(i, node, props, args.width, args.height, args.dir, args.fps, args.quiet)
        workers.append(w)
        if not args.quiet:
            print(f"  + {w.source_id}  {w.label}  node={node}"
                  f"  {w.native_w}x{w.native_h} -> {args.width}x{args.height}", flush=True)
    for w in workers:
        w.start()

    print(f"portal mirror: {len(workers)} window(s) -> {args.dir} at "
          f"{args.width}x{args.height} (cap {args.fps} fps)", flush=True)
    try:
        while True:
            time.sleep(1.0)
            ff.write_manifest_part(args.dir, "portal", [w.manifest_entry() for w in workers])
            if not args.quiet:
                parts = [f"{w.source_id}={w.fps_actual:.1f}fps" + ("" if w.alive else " idle")
                         for w in workers]
                print("  " + "  ".join(parts), flush=True)
            if all(not w.is_alive() for w in workers):
                print("all streams ended", file=sys.stderr)
                return 1
    except KeyboardInterrupt:
        for w in workers:
            w.stop_event.set()
        for w in workers:
            w.join(timeout=3)
        ff.write_manifest_part(args.dir, "portal", [])
        if not args.quiet:
            print("\nstopped")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="mirror open windows into Minecraft via the portal")
    ap.add_argument("--setup", action="store_true", help="pick windows once (interactive)")
    ap.add_argument("--list", action="store_true", help="show the saved token and streams")
    ap.add_argument("--run", action="store_true", help="publish frames for the game")
    ap.add_argument("--fps", type=float, default=15.0, help="cap on frames published per second")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=360)
    ap.add_argument("--dir", default=ff.DEFAULT_DIR)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.setup:
        return cmd_setup(args)
    if args.list:
        return cmd_list(args)
    if args.run:
        return cmd_run(args)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
