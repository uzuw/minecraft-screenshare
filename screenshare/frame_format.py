"""Frame transport format shared by the capture service (Python) and the game side (Java).

Layout of a published frame file (`/dev/shm/mcscreen/<source-id>.frame`):

    offset  size  field
    0       4     magic  = b"MCSF"
    4       1     version = 1
    5       1     pixel format: 1 = RGB24, 2 = RGBA8
    6       2     width  (uint16 LE)
    8       2     height (uint16 LE)
    10      4     sequence number (uint32 LE) - increments once per published frame
    14      8     publish timestamp, milliseconds since epoch (uint64 LE)
    22      ...   pixel data, row-major, top-left origin, no padding

Writers publish atomically: write `<id>.frame.tmp`, then os.replace() onto `<id>.frame`.
Readers therefore never observe a partially written frame; they only need to compare
the sequence number to know whether the pixels changed.

The manifest (`/dev/shm/mcscreen/sources.json`) lists every available source:

    {
      "version": 1,
      "updated_ms": 1730000000000,
      "dir": "/dev/shm/mcscreen",
      "sources": [
        {"id": "mon-eDP-1", "kind": "monitor", "name": "eDP-1",
         "detail": "1920x1080@144.0 at 0,0", "native_w": 1920, "native_h": 1080,
         "out_w": 640, "out_h": 360, "target_fps": 15, "fps": 14.8,
         "last_seq": 412, "last_ts_ms": 1730000000000, "alive": true}
      ]
    }
"""
from __future__ import annotations

import json
import os
import struct
import time

MAGIC = b"MCSF"
VERSION = 1
FMT_RGB24 = 1
FMT_RGBA8 = 2

# Field order must match the layout documented above: magic, version, fmt, w, h, seq(u32), ts(u64).
HEADER = struct.Struct("<4sBBHHIQ")
HEADER_SIZE = HEADER.size  # 22

DEFAULT_DIR = "/dev/shm/mcscreen"
MANIFEST_NAME = "sources.json"
# Producers other than the capture service publish their entries here, one file each.
# sources.json has exactly one writer (the capture service), which merges these in, so
# several producers can contribute sources without clobbering each other.
PARTS_DIR = "manifest.d"
PART_MAX_AGE_MS = 10_000


def frame_path(directory: str, source_id: str) -> str:
    return os.path.join(directory, f"{source_id}.frame")


def pack_frame(width: int, height: int, seq: int, pixels: bytes, fmt: int = FMT_RGB24,
               ts_ms: int | None = None) -> bytes:
    """Build a complete frame file body. Validates the pixel payload length."""
    expected = width * height * (3 if fmt == FMT_RGB24 else 4)
    if len(pixels) != expected:
        raise ValueError(f"pixel payload is {len(pixels)} bytes, expected {expected} "
                         f"for {width}x{height} fmt={fmt}")
    ts = ts_ms if ts_ms is not None else int(time.time() * 1000)
    return HEADER.pack(MAGIC, VERSION, fmt, width, height, seq & 0xFFFFFFFF, ts) + pixels


def unpack_header(data: bytes) -> dict:
    magic, version, fmt, width, height, seq, ts = HEADER.unpack_from(data, 0)
    if magic != MAGIC:
        raise ValueError(f"bad magic {magic!r}")
    return {"version": version, "fmt": fmt, "width": width, "height": height,
            "seq": seq, "ts_ms": ts, "header_size": HEADER_SIZE,
            "payload_size": width * height * (3 if fmt == FMT_RGB24 else 4)}


def publish(directory: str, source_id: str, body: bytes) -> None:
    """Atomically publish a frame: temp file + rename, so readers see whole frames."""
    final = frame_path(directory, source_id)
    tmp = f"{final}.tmp.{os.getpid()}"
    with open(tmp, "wb") as fh:
        fh.write(body)
    os.replace(tmp, final)


def read_frame(directory: str, source_id: str) -> dict | None:
    """Read a published frame. Returns None when it is missing or mid-replace."""
    try:
        with open(frame_path(directory, source_id), "rb") as fh:
            data = fh.read()
    except (FileNotFoundError, OSError):
        return None
    try:
        header = unpack_header(data)
    except (ValueError, struct.error):
        return None
    pixels = data[header["header_size"]:]
    if len(pixels) != header["payload_size"]:
        return None
    header["pixels"] = pixels
    return header


def write_manifest(directory: str, sources: list[dict]) -> None:
    """Compose sources.json from our own sources plus every fresh producer part.

    Producers that are not the capture service (e.g. the portal mirror) announce
    themselves through write_manifest_part(); this function is the single writer of
    sources.json, so those contributions are merged rather than fought over.
    """
    path = os.path.join(directory, MANIFEST_NAME)
    tmp = f"{path}.tmp.{os.getpid()}"
    payload = {"version": VERSION, "updated_ms": int(time.time() * 1000),
               "dir": directory, "sources": sources + read_manifest_parts(directory)}
    with open(tmp, "w") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp, path)


def write_manifest_part(directory: str, producer: str, sources: list[dict]) -> None:
    """Publish this producer's sources for write_manifest() to merge in."""
    parts = os.path.join(directory, PARTS_DIR)
    os.makedirs(parts, exist_ok=True)
    path = os.path.join(parts, f"{producer}.json")
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as fh:
        json.dump({"updated_ms": int(time.time() * 1000), "sources": sources}, fh, indent=2)
    os.replace(tmp, path)


def read_manifest_parts(directory: str) -> list[dict]:
    """Sources from other producers, ignoring parts whose producer has gone away."""
    parts = os.path.join(directory, PARTS_DIR)
    merged: list[dict] = []
    try:
        names = sorted(os.listdir(parts))
    except OSError:
        return merged
    now = int(time.time() * 1000)
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(parts, name)) as fh:
                part = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if now - int(part.get("updated_ms", 0)) > PART_MAX_AGE_MS:
            continue  # stale: that producer stopped refreshing it
        merged.extend(part.get("sources") or [])
    return merged



def read_manifest(directory: str = DEFAULT_DIR) -> dict | None:
    try:
        with open(os.path.join(directory, MANIFEST_NAME)) as fh:
            return json.load(fh)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
