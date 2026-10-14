#!/usr/bin/env bash
# Decompile selected Minecraft classes with the same decompiler the MCP pipeline uses.
#
# Usage: decompile_class.sh <fully.qualified.ClassName> [more names...]
#
# Extracts the class (and its inner classes) from the installed client jar, runs
# Vineflower on just those, and writes Java into notes/decompiled/.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MC="${MC_HOME:-$HOME/.minecraft}"
JAR="$MC/versions/26.2/26.2.jar"
# Vineflower ships as a downloaded jar (tools/fetch_vineflower.sh); override to use your own copy.
VF="${VINEFLOWER_JAR:-$ROOT/tools/vineflower.jar}"
JAVA="$MC/runtime/java-runtime-epsilon/linux/java-runtime-epsilon/bin/java"
WORK="${MC_WORK:-$ROOT/.work}/decompile_work"
OUT="$ROOT/notes/decompiled"

mkdir -p "$WORK" "$OUT"

for fqcn in "$@"; do
  path="${fqcn//.//}"
  base="$(basename "$path")"
  dir="$(dirname "$path")"
  rm -rf "$WORK/$fqcn"
  mkdir -p "$WORK/$fqcn/$dir"

  # the class itself plus any inner/nested classes and lambdas ($1, $2, ...)
  mapfile -t entries < <(unzip -l "$JAR" | awk '{print $4}' | grep -E "^${path}(\$[^/]*)?\.class$" || true)
  if [ "${#entries[@]}" -eq 0 ]; then
    echo "!! no class entries match $fqcn"
    continue
  fi
  for e in "${entries[@]}"; do
    unzip -o -q "$JAR" "$e" -d "$WORK/$fqcn"
  done
  echo "== $fqcn: extracted ${#entries[@]} class files"

  rm -rf "$OUT/$fqcn"
  mkdir -p "$OUT/$fqcn"
  "$JAVA" -Xmx1500m -jar "$VF" \
    --decompile-inner --remove-bridge --decompile-generics --ascii-strings \
    --remove-synthetic --include-classpath --ignore-invalid-bytecode \
    --indent-string='    ' --log-level=WARN \
    "$WORK/$fqcn" "$OUT/$fqcn" 2>&1 | tail -5 || true

  echo "   -> $(find "$OUT/$fqcn" -name '*.java' | wc -l) file(s) in $OUT/$fqcn"
  find "$OUT/$fqcn" -name "*.java" -exec wc -l {} + | tail -3
done
