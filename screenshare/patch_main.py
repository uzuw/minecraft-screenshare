#!/usr/bin/env python3
"""Regenerate a patched `Main` that is otherwise a verbatim copy of the game's own.

The client and server entry points are shadowed only to prove the classpath does what we
think it does: the class prints where it was loaded from, so a launch log shows whether our
copy or Mojang's is running. The rest of the file is untouched game code.

Two things stop the decompiled source from compiling, both fixed here:

  - Vineflower emits references to anonymous inner classes by number (`new 1(...)`), which is
    not valid Java. Rewrite to the binary name (`new Main$1(...)`); `$` is a legal identifier
    character and the jar still supplies those inner classes.
  - the matching `import <fqcn>.1;` lines are not legal either, so they are dropped.

Usage: patch_main.py <decompiled Main.java> <output Main.java> <fully.qualified.Main>
"""
import re
import sys

MAIN_ANCHOR = "    public static void main(final String[] args) {\n"
MARKER = ('        System.out.println("[OUR BUILD] {fqcn} loaded from "\n'
          '            + Main.class.getProtectionDomain().getCodeSource().getLocation());\n')


def fixups(source: str, simple: str, fqcn: str) -> str:
    """Make Vineflower's output valid Java again."""
    source = re.sub(r"\bnew (\d+)\(", rf"new {simple}$\1(", source)
    source = re.sub(r"(^|[^A-Za-z0-9_.$])(\d+)\.([A-Za-z_][A-Za-z0-9_]*)\(",
                    rf"\1{simple}$\2.\3(", source, flags=re.MULTILINE)
    source = re.sub(rf"^import {re.escape(fqcn)}\.\d+;\n", "", source, flags=re.MULTILINE)
    source = re.sub(r"^import org\.jetbrains\.annotations\.\w+;\n", "", source, flags=re.MULTILINE)
    source = re.sub(r"^ *@Contract\([^)]*\)\n", "", source, flags=re.MULTILINE)
    return source


def inject(source: str, fqcn: str) -> str:
    """Stamp the load-origin marker at the top of main(), asserting the anchor is unique."""
    count = source.count(MAIN_ANCHOR)
    if count != 1:
        raise SystemExit(f"main() anchor found {count} times (expected 1) - "
                         f"the game's {fqcn} changed; re-check the hook point")
    return source.replace(MAIN_ANCHOR, MAIN_ANCHOR + MARKER.format(fqcn=fqcn), 1)


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    src, dst, fqcn = sys.argv[1], sys.argv[2], sys.argv[3]
    simple = fqcn.rsplit(".", 1)[-1]
    text = open(src).read()
    if "[OUR BUILD]" in text:
        print(f"{src} is already patched (skipping)")
        return 0
    with open(dst, "w") as fh:
        fh.write(inject(fixups(text, simple, fqcn), fqcn))
    print(f"wrote {dst}")
    print(f"  marker: main() reports which copy of {fqcn} was loaded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
