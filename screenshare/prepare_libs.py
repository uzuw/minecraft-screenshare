#!/usr/bin/env python3
"""Build the runtime classpath for the patched client and extract the LWJGL natives.

`tools/check_launch.py` writes the *game's* library list, which is not enough to launch:

  * the version JSON only lists LWJGL's `natives-linux`/`unsafe` classifiers, never the plain
    `org.lwjgl:lwjgl` artifact, so `org.lwjgl.system.*` is missing and the client dies with
    NoClassDefFoundError: org/lwjgl/system/CallbackI during fillSystemReport;
  * LWJGL loads its .so files from java.library.path, so the natives jars must be unpacked.

Writes screenshare/classpath.txt and screenshare/natives/, then run_client.sh just consumes them.
"""
from __future__ import annotations

import json
import os
import sys
import zipfile

HOME = os.path.expanduser("~")
MC = f"{HOME}/.minecraft"
VERSION = "26.2"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIBS = f"{MC}/libraries"
BASE_CP = f"{ROOT}/notes/classpath.txt"
OUT_CP = f"{ROOT}/screenshare/classpath.txt"
NATIVES_DIR = f"{ROOT}/screenshare/natives"
UNSIGNED_JAR = f"{ROOT}/unsigned/{VERSION}-unsigned.jar"
NATIVE_CLASSIFIER = "natives-linux"


def extra_jars() -> list[str]:
    """Jars the launcher needs that the game's own library list omits."""
    found = []
    for candidate in (
        f"{LIBS}/org/lwjgl/lwjgl/3.4.1/lwjgl-3.4.1.jar",
        f"{LIBS}/org/lwjgl/lwjgl/3.4.1/lwjgl-3.4.1-unsafe.jar",
    ):
        if os.path.isfile(candidate) and candidate not in found:
            found.append(candidate)
    return found


def native_jars() -> list[str]:
    """Every natives-linux jar the version JSON asks for that exists on disk."""
    meta = json.load(open(f"{MC}/versions/{VERSION}/{VERSION}.json"))
    jars = []
    for lib in meta["libraries"]:
        parts = lib["name"].split(":")
        classifier = parts[3] if len(parts) > 3 else None
        if classifier != NATIVE_CLASSIFIER:
            continue
        group, artifact, version = parts[0], parts[1], parts[2]
        path = f"{LIBS}/{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}-{classifier}.jar"
        if os.path.isfile(path):
            jars.append(path)
    return jars


def extract_natives(jars: list[str]) -> int:
    os.makedirs(NATIVES_DIR, exist_ok=True)
    written = 0
    for jar in jars:
        with zipfile.ZipFile(jar) as zf:
            for name in zf.namelist():
                if "/" in name or not (name.endswith(".so") or ".so." in name):
                    continue
                target = os.path.join(NATIVES_DIR, name)
                if os.path.isfile(target) and os.path.getsize(target) == zf.getinfo(name).file_size:
                    continue
                with zf.open(name) as src, open(target, "wb") as dst:
                    dst.write(src.read())
                written += 1
    return written


def main() -> int:
    if not os.path.isfile(BASE_CP):
        print(f"missing {BASE_CP} - run: python3 tools/check_launch.py", file=sys.stderr)
        return 1
    base = [p for p in open(BASE_CP).read().strip().split(":") if p]
    vanilla = f"{MC}/versions/{VERSION}/{VERSION}.jar"
    base = [UNSIGNED_JAR if p == vanilla else p for p in base]
    if not os.path.isfile(UNSIGNED_JAR):
        print(f"warning: {UNSIGNED_JAR} missing - run tools/unsign_jar.py (using the signed jar)",
              file=sys.stderr)
        base = [vanilla if p == UNSIGNED_JAR else p for p in base]

    extras = extra_jars()
    if not extras:
        print("warning: no LWJGL core jar found - the client will fail to start", file=sys.stderr)

    entries = [f"{ROOT}/patched/out"] + base + [e for e in extras if e not in base]
    with open(OUT_CP, "w") as fh:
        fh.write(":".join(entries))

    jars = native_jars()
    written = extract_natives(jars)
    print(f"classpath : {len(entries)} entries -> {OUT_CP}")
    print(f"  + extras: {', '.join(os.path.basename(e) for e in extras)}")
    print(f"natives   : {len(jars)} jar(s), {written} new file(s) -> {NATIVES_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
