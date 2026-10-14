"""Can we launch Minecraft 26.2 ourselves (own classpath, own code shadowing the game)?

Resolves the local version JSON's library list against ~/.minecraft/libraries,
locates Mojang's bundled Java runtime, and reports exactly what is present /
missing. This is the readiness check for the "patch the game without the 8 GB
Gradle pipeline" route.
"""
import json
import os
import platform

MC = os.path.expanduser("~/.minecraft")
VERSION = "26.2"
JSON_PATH = f"{MC}/versions/{VERSION}/{VERSION}.json"
LIBS = f"{MC}/libraries"

meta = json.load(open(JSON_PATH))
print(f"version json : {JSON_PATH}")
print(f"id           : {meta['id']}  mainClass: {meta['mainClass']}")
print(f"assets index : {meta['assetIndex']['id']}")

# ---- rules for library artifacts ------------------------------------------
os_name = {"linux": "linux", "darwin": "osx", "windows": "windows"}[
    "linux" if platform.system() == "Linux" else platform.system().lower().replace("darwin", "darwin")
]
arch = "64" if platform.machine() in ("x86_64", "amd64") else platform.machine()


NATIVE_CLASSIFIER = "natives-linux"


def rules_allow(lib):
    """Evaluate launcher OS/arch rules; unknown rule kinds count as non-matching."""
    rules = lib.get("rules")
    if not rules:
        return True
    allowed = False
    for r in rules:
        action = r.get("action")
        os_rule = r.get("os", {})
        ok = True
        if "name" in os_rule:
            ok = ok and os_rule["name"] == os_name
        if "arch" in os_rule:
            ok = ok and os_rule["arch"] == "x86_64"
        if "version" in os_rule:
            ok = False  # version-range rules are not evaluated in this check
        if ok:
            allowed = action == "allow"
    return allowed


def maven_path(name):
    """On-disk jar path from maven coordinates: group:artifact:version[:classifier]."""
    parts = name.split(":")
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else None
    fname = f"{artifact}-{version}" + (f"-{classifier}" if classifier else "") + ".jar"
    return f"{group.replace('.', '/')}/{artifact}/{version}/{fname}"


def lib_path(lib):
    """26.2 version JSONs carry no artifact 'path'; derive it from the URL or maven name."""
    a = lib["downloads"]["artifact"]
    url = a.get("url", "")
    path = a.get("path") or (url.split("libraries.minecraft.net/")[-1] if "libraries.minecraft.net" in url else maven_path(lib["name"]))
    return f"{LIBS}/{path}", a.get("size"), a.get("sha1")


present, missing, natives = [], [], []
for lib in meta["libraries"]:
    name = lib["name"]
    parts = name.split(":")
    classifier = parts[3] if len(parts) > 3 else None
    if classifier:  # modern JSONs list natives as their own entries, one per platform
        if classifier != NATIVE_CLASSIFIER:
            continue
    elif not rules_allow(lib):
        continue
    if "artifact" not in lib.get("downloads", {}):
        continue
    p, size, sha1 = lib_path(lib)
    ok = os.path.exists(p) and os.path.getsize(p) > 0
    (present if ok else missing).append((name, p, size))
    if classifier:
        natives.append((name, p, ok))

print(f"\nlibraries in json      : {len(meta['libraries'])}")
print(f"applicable artifacts   : {len(present) + len([m for m in missing if 'native' not in m[0]])}")
print(f"present on disk        : {len(present)}")
print(f"missing                : {len(missing)}")
for name, p, size in missing[:25]:
    print(f"   MISSING {name}  ({round((size or 0)/1e6,2)} MB)")
total_missing = sum((s or 0) for _, _, s in missing)
print(f"missing download size  : {round(total_missing/1e6,1)} MB")

# ---- java runtime ---------------------------------------------------------
print("\n=== Mojang runtime ===")
for root, dirs, files in os.walk(f"{MC}/runtime"):
    depth = root[len(f"{MC}/runtime"):].count(os.sep)
    if depth > 4:
        dirs[:] = []
        continue
    if "java" in files:
        print("  java:", os.path.join(root, "java"))
    if "release" in files:
        rel = os.path.join(root, "release")
        try:
            txt = open(rel).read()
            version = txt.split("JAVA_VERSION=")[1].split('"')[1]
            print("  release file:", rel, "-> JAVA_VERSION", version)
        except Exception as e:
            print("  release file:", rel, "(unparsed:", e, ")")

# ---- client jar + assets --------------------------------------------------
print("\n=== client jar / assets ===")
jar = f"{MC}/versions/{VERSION}/{VERSION}.jar"
print(f"  client jar     : {jar}  {round(os.path.getsize(jar)/1e6,1)} MB")
idx = f"{MC}/assets/indexes/{meta['assetIndex']['id']}.json"
print(f"  asset index    : {idx}  {'PRESENT' if os.path.exists(idx) else 'MISSING'}")
objs = f"{MC}/assets/objects"
n_obj = sum(len(f) for _, _, f in os.walk(objs)) if os.path.isdir(objs) else 0
print(f"  asset objects  : {n_obj:,} files ({round(sum(os.path.getsize(os.path.join(r,f)) for r,_,fs in os.walk(objs) for f in fs)/1e6,1)} MB)")
print(f"  log config     : {meta.get('logging', {}).get('client', {}).get('file', {}).get('id')}")

# ---- a ready-to-use classpath we control ----------------------------------
cp = [jar] + [p for _, p, _ in present]
NOTES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "notes")
os.makedirs(NOTES, exist_ok=True)
with open(os.path.join(NOTES, "classpath.txt"), "w") as f:
    f.write(":".join(cp))
print(f"\nclasspath ({len(cp)} entries) written to notes/classpath.txt")
print("first entries:", ":".join(os.path.basename(x) for x in cp[:6]), "...")
