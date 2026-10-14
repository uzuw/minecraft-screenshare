"""Write an unsigned copy of a signed jar so our own classes can share its packages.

Minecraft's client jar is signed. Putting our compiled class before a *signed* jar on
the classpath fails with:

    SecurityException: class "X$1"'s signer information does not match signer
    information of other classes in the same package

because JVM signing rules require every class in a package to have the same signer.
Mod loaders (Forge/Fabric) solve this the same way: run against an unsigned copy.

This only removes integrity signatures from our local copy -- it does not bypass
licensing, DRM, or ownership checks.

Usage: python3 unsign_jar.py <signed.jar> <unsigned.jar>
"""
import os
import sys
import zipfile

SIGNATURE_SUFFIXES = (".SF", ".RSA", ".DSA", ".EC")


def unsign(src, dst):
    removed = []
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            name = item.filename
            if name.startswith("META-INF/") and (name.upper().endswith(SIGNATURE_SUFFIXES) or name == "META-INF/MANIFEST.MF"):
                removed.append(name)
                continue
            zout.writestr(item, zin.read(name))
    return removed


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    removed = unsign(src, dst)
    print(f"removed {len(removed)} signature/manifest entries: {removed}")
    print(f"wrote {dst} ({os.path.getsize(dst)/1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
