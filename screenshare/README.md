# In-world screen share

Live desktop feeds rendered as **flat panels inside the Minecraft 3D world** — depth-tested,
occluded by blocks, and fogged like any other world geometry (not a HUD overlay).

Built on [MCP-Reborn](https://github.com/Hexeption/MCP-Reborn) (decompiled Minecraft **26.2**),
patched by hand — **no mod loader is involved**. Our classes sit first on the classpath and
shadow the game's own.

## Opening it

```bash
screenshare/play.sh              # start everything and open the game
screenshare/play.sh --status     # what is running, and which screens the game can place
screenshare/play.sh --stop       # stop the game and the capture service
screenshare/play.sh --world NAME # open a particular world (default: the last one used)
screenshare/play.sh --rebuild    # recompile the patched classes first
```

`screenshare/install_desktop.sh` adds clickable launchers (**Minecraft (screen-share)** and a
stop entry) to your application menu, generated from the repo so the paths stay correct.

One command does all of this, in order:

1. starts exactly one capture service (a stray second one would race on the manifest),
2. restores every recorded screen channel — Hyprland renumbers headless outputs and drops them
   on logout, so `app_screens.sh restore` recreates them and brings each app back,
3. launches the patched client straight into a world, since the menu is the only part that needs
   a mouse.

In game: `F7` places a panel where you look, `N` cycles which source it shows, `F8` toggles edit
mode, `+` `-` resize, `;` `'` tilt, arrows move, `DEL` removes, `R` resets the layout.

### Screen ids are stable, and why a panel may need one `N` press

A channel publishes `mon-<channel-name>` (e.g. `mon-youtube`), not `mon-<output-name>`: Hyprland
hands out a different name for a headless output after every restart, so an id built from the
output name would move out from under a panel already placed in the world. A panel therefore
keeps working across restarts.

Measured caveat: if a panel's bound source has *disappeared* from the registry (a channel was
removed or renamed, as happened when this id scheme landed), pressing `N` does not recover it —
the cycle has no valid source to advance from. Remove the panel with `DEL` and place a new one
with `F7`, then `N` to the wanted screen. Editing `gamedir/screenshare.json` and restarting also
works and is what to do for several panels at once.

```
 ┌─────────────────────────────┐        ┌──────────────────────────┐
 │ capture_service.py (Python) │        │  patched client (Java)   │
 │  grim (wlr-screencopy)      │        │  com.uzu.screenshare.*   │
 │  → PIL BOX downscale        │        │  FrameReader → texture   │
 │  → 640x360 RGB24 @ 15 fps   │        │  WorldScreens.submit()   │
 └──────────────┬──────────────┘        └────────────▲─────────────┘
                │ atomic rename                       │ poll + seq compare
                ▼                                     │
      /dev/shm/mcscreen/<source-id>.frame  ───────────┘
      /dev/shm/mcscreen/sources.json  (manifest: fps, alive, native size)
```

## Layer 1 — capture (`capture_service.py`)

Hyprland with no `xdg-desktop-portal` installed means there is no PipeWire ScreenCast route, so
capture goes through **`grim` / wlr-screencopy**.

Measured on this host: `grim -t ppm` (raw, no PNG decode) is the fast path — **~37 ms** for
1920x1080 versus **~187 ms** for PNG. PIL decodes PPM in ~3 ms and a BOX downscale to the panel
size costs ~12 ms, giving **~15 fps per source**. Each source gets its own thread, so N sources
scale across cores.

Frames are published to `/dev/shm`, so the game reads RAM and never touches the disk. The service
re-scans every 5 s, so monitors hot-plugged after start are picked up live.

```bash
python3 screenshare/capture_service.py --list                  # enumerate sources
python3 screenshare/capture_service.py --once --preview out/    # one frame + PNG previews
python3 screenshare/capture_service.py --fps 15 --sources all   # run as a daemon
```

Two source kinds:

| Kind | Capture | Caveat |
|------|---------|--------|
| `mon-<name>` | `grim -o <output>` | none |
| `win-<addr>` | `grim -g "x,y wxh"` (region) | **reads whatever the compositor shows at that rectangle — an occluded window yields the window on top of it.** Monitor sources are the reliable choice. |

## Layer 2 — frame transport (`frame_format.py` ⇄ `FrameReader.java`)

A 22-byte header, then raw pixels:

| offset | size | field |
|--------|------|-------|
| 0 | 4 | magic `MCSF` |
| 4 | 1 | version |
| 5 | 1 | pixel format (1 = RGB24, 2 = RGBA8) |
| 6 | 2 | width (uint16 LE) |
| 8 | 2 | height (uint16 LE) |
| 10 | 4 | sequence number (uint32 LE) |
| 14 | 8 | publish timestamp, ms since epoch (uint64 LE) |
| 22 | … | pixels, row-major, top-left origin, no padding |

Writers publish **atomically** — write `<id>.frame.tmp`, then `os.replace()` onto `<id>.frame` — so
a reader can never observe a half-written frame. The game only uploads a new GPU texture when the
**sequence number** changes, which keeps a static desktop nearly free.

## Layer 3 — the client patch (Java)

`patched/src/com/uzu/screenshare/`:

| File | Role |
|------|------|
| `CaptureService`…`SourceRegistry.java` | reads the manifest, polls frames, owns one `ScreenSurface` per source, uploads textures on the render thread |
| `ScreenSurface.java` | staged pixel buffer → `DynamicTexture`, resize detection |
| `WorldScreen.java` | one placed panel: position, yaw, pitch, size, dimension, quad emission |
| `WorldScreens.java` | placement, edit mode, persistence, and the world-space draw call |
| `LayoutStore.java` | saves/loads panels to `<gameDir>/screenshare.json` |
| `ScreenShareClient.java` | the two entry points the game calls (tick + HUD extract) |
| `FrameReader.java` | the Java half of the frame format above |
| `Keys.java` | key polling with edge detection |

Two hook sites are injected into decompiled game classes by asserted patch scripts:

* **`patch_level_renderer.py`** → `WorldScreens.submit()` immediately after `submitFeatures()`.
  This is what makes the panels part of the world pipeline instead of an overlay.
* **`patch_gui.py`** → `Gui.tick()` → `ScreenShareClient.tick()`, and
  `Gui.extractRenderState()` → `ScreenShareClient.extract()`.

Both scripts **assert** that each anchor appears exactly once, so a Minecraft update fails loudly
instead of silently producing a build with no hooks. They also repair things Vineflower emits that
javac cannot compile: numeric anonymous-class names (`new Gui$1(...)`), and anonymous classes that
must be *inlined* because javac hides an anonymous class's synthetic constructor parameters when
reading one from a class file.

## Per-application screens

Each application can have **its own screen**, one panel per app — Chrome on one, Spotify on
another. There are two ways to get that, and they differ in one important way.

### Region capture (`--sources windows`) — ids are per app, pixels may not be

`capture_service.py --sources windows` publishes one source per application window, keyed by
app class: `win-google-chrome`, `win-spotify`, `win-google-chrome-2` for a second window of the
same app. The id is deliberately **not** the window address (which changes on every launch and
would orphan a panel already placed in the world), and each window's rectangle is re-read twice
a second so a window that is moved or resized keeps being captured correctly.

**The catch:** these are *region* captures (`grim -g <rect>`), which return whatever the
compositor shows at that rectangle. Measured on this host, four maximised windows all reported
the same rectangle (`1908x1023 at 6,51`) and all four sources came back as **the same picture**,
so "one screen per app" was cosmetic. Region capture is only genuinely per-app when the window
occupies a screen area nothing else covers.

### Screen channels (`app_screens.sh`) — genuinely per application

A window alone on its own output has no such problem: it is the only thing there, so capturing
that output *is* capturing the application, occlusion-free. The capture service already
enumerates monitors, so such a channel appears in the game as `mon-<output>` with **no changes
to the Java side**.

```bash
screenshare/app_screens.sh windows                        # what could go on a screen
screenshare/app_screens.sh attach youtube google-chrome   # use a window ALREADY open
screenshare/app_screens.sh add  spotify -- spotify        # or launch one onto a new screen
screenshare/app_screens.sh move youtube --title "YouTube" # change which window it shows
screenshare/app_screens.sh list                           # screens + the game source id
screenshare/app_screens.sh remove youtube                 # tear it down
```

**`attach` is usually what you want**: it takes an application you already have running and
gives it its own screen — no second copy, no separate profile, your existing session untouched.
`add` is for launching something new onto a screen. If two windows of the same class match (two
Chrome windows, say) the command **refuses to guess** and prints them, so add `--title <part of
the title>` or `--address <prefix>` to choose exactly. With no selector at all it takes the
window you are looking at, read from the compositor.

Windows are addressed by **selector**, as in
`hyprctl dispatch 'hl.dsp.window.move({ workspace = 4, window = "address:0x..." })'`. The earlier
approach — parking the pointer in the target to focus it, then moving the focused window —
was measured to move the *wrong* window whenever the target was not already focused, because
`ydotool --absolute` does not land where you compute (uinput absolute axes). The channel then
held one of the user's Chrome windows instead of the intended one. Every move now names its
window and is verified afterwards; on failure the channel is not left half-built.

Then in game: place a panel (`F7`) and press `N` until it reads `mon-HEADLESS-<n>`.

Measured behaviour on Hyprland 0.56: creating a headless output and launching an app while it
is focused puts the app on that output, and `grim -o <output>` returns that application's pixels
(verified: a Chrome window whose tab title read "YouTube", with the desktop's wallpaper and bars
absent). Two things to know:

* **Dispatchers are Lua in 0.56.** `hyprctl dispatch 'hl.dsp.window.move({ workspace = 5 })'`
  works; the older `hyprctl dispatch movetoworkspacesilent 5` is a Lua syntax error.
* `move` acts on the **focused** window: click the window you want, then run it. The
  by-address selector forms (`{ window = { address = ... } }`) are rejected by this build.
* Output names are **not stable** — Hyprland renumbers headless outputs, and they do not survive
  a reboot. `app_screens.sh` always discovers the name and remembers it in `channels.json`, but a
  panel placed in the world binds to `mon-HEADLESS-<n>`, so it may need re-binding after a
  restart. Binding panels to a channel *name* instead is the outstanding follow-up.

## Controls

| Key | Action |
|-----|--------|
| **F7** | Place a panel where you are looking, bound to the next unused source. Snaps to the block face under the crosshair, else floats 4 blocks ahead. |
| **F8** | Toggle edit mode |
| **TAB** | Select next panel |
| **Arrows** | Move along the panel's own right/up axes |
| **+ / -** | Resize |
| **[ / ]** | Rotate yaw (15° steps) |
| **; / '** | Tilt pitch |
| **N** | Cycle the panel's capture source |
| **DEL** | Remove |
| **R** | Re-level every panel in front of the player |

Layouts persist per-dimension in `<gameDir>/screenshare.json` and save themselves 2 s after any
change.

## Running it

```bash
bash screenshare/start_stack.sh          # capture service (detached) + patched client
bash screenshare/start_stack.sh --no-client   # capture service only
```

Useful environment variables:

| Variable | Effect |
|----------|--------|
| `MC_SCREENSHARE_GAMEDIR` | game directory (default `screenshare/gamedir`) |
| `MC_SCREENSHARE_WORLD=<save>` | jump straight into a singleplayer world |
| `MC_SCREENSHARE_DEBUG_KEYS=1` | log held key codes |
| `MC_SCREENSHARE_WAYLAND=1` | unset `DISPLAY` for a Wayland-native GLFW build |

Launch is offline (`--accessToken 0`) and uses its own game dir, so it does not touch your real
Minecraft profile.

## Bootstrapping a fresh clone

The repo deliberately excludes derived Mojang artifacts (see the note at the bottom). Rebuild them
in this order:

```bash
bash tools/fetch_vineflower.sh                                   # 0. decompiler jar (pinned, checksum-verified)
python3 tools/check_launch.py                                    # 1. notes/classpath.txt
python3 tools/unsign_jar.py                                      # 2. unsigned/26.2-unsigned.jar
bash tools/decompile_class.sh net.minecraft.client.gui.Gui       # 3. notes/decompiled/**
bash tools/decompile_class.sh net.minecraft.client.renderer.LevelRenderer
bash screenshare/build.sh                                        # 4. patched/src + patched/out
python3 screenshare/prepare_libs.py                              # 5. classpath.txt + natives/
bash screenshare/start_stack.sh                                  # 6. go
```

## Keeping the desktop smooth (memory and CPU)

Launching the game used to make the whole session stutter: windows glitched and the Qt bar broke
up. It was not the compositor, the driver, or the virtual outputs — it was **RAM**. This machine
has 15.7 GB, and *before* the game starts the desktop is already holding ~12 GB (browser ~4 GB,
the agent stack ~2.6 GB, editors/node ~1.9 GB, Electron ~1.2 GB, Discord ~0.9 GB, Spotify
~0.9 GB). Swap here is **zram** — compressed RAM — and it was already at 3.9 GB. Once the launch
spike pushed past what was free, the kernel spent its time compressing and decompressing pages,
and every process that wanted to run stuttered. The Qt bar glitching is a symptom of memory
pressure, not of the bar itself (it burned 0.0–0.2 s of CPU during the whole launch).

Measured with two real launches of the client, 90 s each, capture service running in both
(the "new" run started with **817 MB less** free memory, so this understates the difference):

| | old JVM config | current | change |
|---|---|---|---|
| JVM peak RSS | 1119 MB | **833 MB** | **−26 %** |
| pages swapped out | 27 789 (108 MB) | **1 941 (7 MB)** | **−93 %** |
| pages swapped in | 3 057 | 765 | −75 % |
| major faults (each stalls a process) | 4 286 | **852** | **−80 %** |
| swap used | +109 MB | +4 MB | |
| reached the title screen | ✓ | ✓ | |

Four changes, all in this directory:

1. **The game's heap** (`run_client.sh`): 1536 MB instead of 2 GB, 256 MB committed instead of
   512 MB, SerialGC instead of G1, and caps on metaspace and the JIT code cache. Override with
   `MC_SCREENSHARE_XMX`.
2. **Cheap virtual screens** (`app_screens.sh`, `shrink_output`): a channel's headless output is
   1280x720 instead of 1920x1080. The in-world panel only ever shows 640x360, so 1080p was 9× more
   pixels than could be displayed — and an output is a real compositor surface whose buffers come
   out of system RAM on this iGPU (no dedicated VRAM). Override with
   `MC_SCREENSHARE_CHANNEL_MODE=1920x1080@60`.
3. **Capture yields** (`capture_service.py`, `play.sh`, `start_stack.sh`): the service runs at
   `nice -n 10` and each `grim` at `nice -n 15`, so capture always loses the CPU fight to the
   compositor and the game. Default rate is 12 fps per source rather than 15.
4. **Do not use `grim -s`**: measured 111 ms/frame against 37 ms, three times *slower* — asking for
   a scaled buffer makes the compositor render a scaled pass. Shrink the output instead.

Still worth knowing: a full-screen desktop source (`mon-eDP-1`) is the expensive one at
37 ms/frame (~44 % of a core at 12 fps) versus 21 ms for a 720p virtual output. If you only want
app screens in-world, run the service with `--sources windows`, or keep the number of channels
small. And no tuning under this much pressure beats **having the RAM**: closing the browser when
playing is worth more than every option above.

## Caveats

* **GLFW is X11-only** in this build, so the client runs through XWayland. XWayland windows do not
  receive Wayland virtual-keyboard events, so **scripted input must go through `ydotool` (uinput)
  rather than `wtype`** — see `layout_probe.py`.
* Window sources are region captures and misbehave when occluded (see Layer 1).
* Keys are **polled** once per client tick — the game exposes no key-listener hook to a patched
  client without a mod loader. Human input is fine; scripted input must *hold* each key rather than
  sending an instant press/release.
* Anything that changes `Gui` or `LevelRenderer` shape between versions breaks the patch anchors by
  design. Re-check the hook points and update the `require(...)` counts.

## Not in the repo, on purpose

Excluded to avoid committing Mojang's copyrighted binaries and decompiled source, and to keep the
repo small — all of it is reproducible with the commands above:

| Path | Why | Regenerate with |
|------|-----|-----------------|
| `unsigned/*.jar` | the game jar, 33 MB | `tools/unsign_jar.py` |
| `notes/decompiled/**` | decompiled game source | `tools/decompile_class.sh` |
| `patched/src/net/minecraft/**` | generated patched game classes | `screenshare/build.sh` |
| `patched/out/**` | build output | `screenshare/build.sh` |
| `screenshare/natives/` | extracted LWJGL `.so` files | `screenshare/prepare_libs.py` |
| `screenshare/classpath.txt` | absolute local paths | `screenshare/prepare_libs.py` |
| `tools/vineflower.jar` | third-party decompiler binary | `tools/fetch_vineflower.sh` |
| `screenshare/gamedir/` | smoke-test worlds and runtime state | written by the game |

The verification screenshots in `layouttest/` are captured from this machine's own desktop, so they
contain whatever was on screen at the time.