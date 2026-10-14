# Per-application screens ("channels") — verified findings

**Goal (decided 2026-10-14):** each application gets its own individual screen source, one
Minecraft-placeable panel per application. **View-only — no input control.** Control was dropped
because injected input goes to the *focused* window, so driving an in-world screen means Minecraft
cannot be focused, i.e. it cannot be played.

This file records what was *measured on this machine*, including the two assumptions that turned
out to be wrong. Everything below was run against the live session.

---

## 1. VERIFIED WORKING: a virtual display becomes a screen source with zero code

```
$ hyprctl output create headless
ok
$ hyprctl monitors -j          # this is the ONLY thing capture_service.list_monitors() reads
  id=0  name=eDP-1        1920x1080 at 0,0     focused=True
  id=1  name=HEADLESS-1   1920x1080 at 1920,0  focused=False
$ grim -o HEADLESS-1 -t ppm -   # exactly what the service runs
  first 2 bytes: 'P6'  -> CAPTURE WORKS
```

With the existing `capture_service.py --sources monitors` running, its 5 s hotplug re-scan spawns a
worker for it unprompted:

```
/dev/shm/mcscreen/mon-eDP-1.frame        691222 bytes
/dev/shm/mcscreen/mon-HEADLESS-1.frame   691222 bytes   (22-byte header + 640x360x3, exact)
manifest:  id=mon-HEADLESS-1  kind=monitor  native=1920x1080 -> out=640x360  15.0fps  alive=True  errors=0
```

So the Java side needs **no changes** to render a channel: it already enumerates the manifest, and
`F7` places a panel bound to the next unused source while `N` cycles a panel's source.

`1920x1080 -> 640x360` is an exact **3:1** integer reduce, so `downscale()` takes the cheap
`img.reduce(3)` path (~12 ms), not `resize()`. Channel resolution therefore needs no tuning.

## 2. WRONG #1: a channel output is not a clean canvas

I claimed a channel would be private "by construction". **False.** `grim -o HEADLESS-1` on an empty
channel showed the full desktop chrome: the anime wallpaper, the top bar with workspace numbers, the
clock (`2:30 pm · Wed, 14/10`, battery `100`), and **the now-playing track title** from the media
widget.

Cause: **quickshell is running** (end-4 / illogical-impulse dotfiles) and draws bar + widgets +
wallpaper on *every* output, and `~/.config/hypr/hyprland.lua` line 72 has a wildcard monitor rule:

```lua
monitor = ", preferred, auto, 1, refresh_rate = 60"
```

A channel is therefore *less* leaky than sharing the whole desktop, but it still exposes wallpaper,
bar widgets and media metadata. Making it clean needs either per-output suppression in quickshell or
a compositor that has no such chrome at all.

## 3. WRONG #2: focus-then-launch does not place a window on the channel

`hyprctl dispatch focusmonitor HEADLESS-1; launch-app; focusmonitor eDP-1` **failed**: Chrome took
long enough to map its first window that focus had already returned, so the window landed on
`eDP-1` (monitor id 0) — i.e. on the real screen. Confirmed via `hyprctl clients -j`.

Placement must be explicit instead:
* a `windowrule` in the Lua config binding a class to the channel output, or
* keep the channel focused until the window maps (poll `hyprctl clients -j`), or
* launch, then move the window's workspace onto the output.

## 4. Architecture note: the producer is swappable

The Java side consumes `/dev/shm/mcscreen/<id>.frame` + `sources.json`. It does not care how frames
are produced. So a completely different producer — e.g. xdg-desktop-portal ScreenCast with a
**Window** source (damage-driven, occlusion-proof) — can be added later as an extra source kind
without touching `patched/src/com/uzu/screenshare/`.

Measured on this host: the portal is live (`xdg-desktop-portal-hyprland` running, ScreenCast
interface exposed, `AvailableSourceTypes = 7` = Monitor | Window | Virtual), and
`gst-inspect-1.0 pipewiresrc` works, so a PipeWire consumer is feasible.

**Correction to code:** `capture_service.py`'s docstring says "With no xdg-desktop-portal installed,
the PipeWire ScreenCast route is unavailable". The portal IS installed and running — that rationale
is stale, and `screenshare/README.md` repeats it.

## 5. Remaining work, in order

| # | Task | Why it is needed | Est. |
|---|------|------------------|------|
| 1 | **Channel definition + naming.** A helper that creates an output per named channel (`youtube`, `spotify`, ...), records the name?output mapping, and tears it down. | `HEADLESS-N` is not stable: fake outputs are runtime-only (**they do not survive reboot** — verified: `HEADLESS-1` vanished in the 2026-10-14 reboot), and Hyprland numbers them sequentially, so a panel pinned to `mon-HEADLESS-1` can show the wrong channel. | small |
| 2 | **Bind panels to a channel *name*, not a source id.** `LayoutStore` persists `sourceId`; resolve channel?source at render time. | Panels then survive reboot and output renumbering. | small |
| 3 | **Explicit window placement** onto a channel output (window rule or map-wait). | See WRONG #2. | small |
| 4 | **Clean the channel canvas.** Per-output quickshell suppression + a plain background, *or* a pristine headless compositor (`sway` with `WLR_BACKENDS=headless`, or `cage` — **neither installed**). | See WRONG #1. Touches the user's dotfiles, so it needs sign-off. | medium |
| 5 | **Autostart.** `start_stack.sh` needs re-running by hand; the service does not survive reboot (systemd user unit). | After the reboot, `/dev/shm/mcscreen` was gone and the game had no sources. | small |

## 6. Cost per channel (measured)

Capture + downscale is ~24 ms/frame (12 ms `grim` PPM + 12 ms BOX reduce) at 15 fps, one thread
each, independent of whether the picture changed — `grim` is full-frame with no damage tracking.
That is ~36% of one core per channel. Fine for a handful on this 16-thread i5-12500H; it is the
argument for moving to a damage-driven producer (§4) once channel count grows.

## 7. Open question

Is "each application" meant as **dedicated instances on their own virtual display** (a real TV
channel: always that app, nothing else), or as **mirrors of the real windows already open** (portal
Window source)? Both are reachable; they are different products, and §4 means choosing one does not
block the other.
