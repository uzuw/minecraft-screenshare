#!/usr/bin/env python3
"""Regenerate the patched net.minecraft.client.renderer.LevelRenderer with the screen hook.

The hook goes between the world collecting its geometry and that geometry being prepared:

    this.submitFeatures(this.levelRenderState, this.submitNodeStorage, renderOutline);
    <-- WorldScreens.submit(this.submitNodeStorage, this.levelRenderState, Minecraft.getInstance())
    PreparedFrame featureFrame = this.featureRenderDispatcher.prepareFrame(this.submitNodeStorage);

That is what makes our screens part of the world pipeline (depth tested, occluded by blocks,
fogged) rather than an overlay.

Fixups needed for this class (all asserted, so a game update fails loudly):
  * Vineflower's numeric anonymous-class references -> `LevelRenderer$N` binary names
  * `new LevelRenderer$1(this, profiler)` cannot compile: javac hides an anonymous class's
    synthetic constructor parameters when reading one from a class file. The class is inlined
    as `ProfilerInspector` and the call site rewritten.
  * the nested record `FinalizedGizmos` cannot be resolved from the jar either - inlined.
  * `import net.minecraft.client.renderer.LevelRenderer.FinalizedGizmos;` (a self-import of a
    nested class) is invalid in the recompiled source - dropped.
  * `new Draw(...)` is raw in the decompiled output, so the uploader lambda's parameter infers
    as Object and `sectionUbos[finalUboIndex]` fails - parameterised as `Draw<GpuBufferSlice[]>`.

Usage: patch_level_renderer.py <decompiled LevelRenderer.java> <output LevelRenderer.java>
"""
import re
import sys

FQCN = "net.minecraft.client.renderer.LevelRenderer"
IMPL = "com.uzu.screenshare.WorldScreens"

ANCHOR = (
    "        this.submitFeatures(this.levelRenderState, this.submitNodeStorage, renderOutline);\n"
    "        profiler.popPush(\"prepareFeatures\");\n"
)
HOOK = (
    "        this.submitFeatures(this.levelRenderState, this.submitNodeStorage, renderOutline);\n"
    f"        {IMPL}.submit(this.submitNodeStorage, this.levelRenderState, "
    "net.minecraft.client.Minecraft.getInstance());\n"
    "        profiler.popPush(\"prepareFeatures\");\n"
)

ANON_CALL_SITE = "new LevelRenderer$1(this, profiler)"
ANON_REPLACEMENT = "new ProfilerInspector(this, profiler)"
RAW_DRAW = "new Draw("
TYPED_DRAW = "new Draw<GpuBufferSlice[]>("

INLINED = '''
    /** Inlined from the game's nested record LevelRenderer$FinalizedGizmos. */
    record FinalizedGizmos(
        net.minecraft.client.renderer.gizmos.DrawableGizmoPrimitives standardPrimitives,
        net.minecraft.client.renderer.gizmos.DrawableGizmoPrimitives alwaysOnTopPrimitives
    ) {
    }

    /** Inlined from the game's anonymous frame-graph inspector (LevelRenderer$1). */
    private static final class ProfilerInspector
            implements com.mojang.blaze3d.framegraph.FrameGraphBuilder.Inspector {
        private final ProfilerFiller profiler;

        ProfilerInspector(final LevelRenderer owner, final ProfilerFiller profiler) {
            this.profiler = profiler;
        }

        @Override
        public void beforeExecutePass(final String name) {
            this.profiler.push(name);
        }

        @Override
        public void afterExecutePass(final String name) {
            this.profiler.pop();
        }
    }
'''


def require(source: str, needle: str, expected: int, what: str) -> None:
    count = source.count(needle)
    if count != expected:
        raise SystemExit(f"expected {expected} occurrence(s) of {what}, found {count} - "
                         f"the game's {FQCN} changed")


def fixups(source: str) -> str:
    """Make Vineflower's output valid Java again."""
    source = re.sub(r"\bnew (\d+)\(", r"new LevelRenderer$\1(", source)
    source = re.sub(r"(^|[^A-Za-z0-9_.$])(\d+)\.([A-Za-z_][A-Za-z0-9_]*)\(",
                    r"\1LevelRenderer$\2.\3(", source, flags=re.MULTILINE)
    source = re.sub(rf"^import {re.escape(FQCN)}\.\d+;\n", "", source, flags=re.MULTILINE)
    source = re.sub(r"^import org\.jetbrains\.annotations\.\w+;\n", "", source, flags=re.MULTILINE)
    source = re.sub(r"^ *@Contract\([^)]*\)\n", "", source, flags=re.MULTILINE)
    source = source.replace(f"import {FQCN}.FinalizedGizmos;\n", "")

    require(source, ANON_CALL_SITE, 1, "the anonymous inspector construction")
    source = source.replace(ANON_CALL_SITE, ANON_REPLACEMENT, 1)

    require(source, RAW_DRAW, 1, "the raw Draw construction")
    source = source.replace(RAW_DRAW, TYPED_DRAW, 1)
    return source


def inject(source: str) -> str:
    require(source, ANCHOR, 1, "the geometry-collection anchor")
    return source.replace(ANCHOR, HOOK, 1)


def append_inlined(source: str) -> str:
    end = source.rstrip().rfind("}")
    if end < 0:
        raise SystemExit("could not find the end of the class")
    return source[:end] + INLINED + source[end:]


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    text = open(src).read()
    if IMPL in text:
        print(f"{src} is already patched (skipping)")
        return 0
    text = append_inlined(inject(fixups(text)))
    with open(dst, "w") as fh:
        fh.write(text)
    print(f"wrote {dst}")
    print(f"  hook: {IMPL}.submit(...) after submitFeatures()")
    print("  inlined: FinalizedGizmos (record) + ProfilerInspector (was LevelRenderer$1)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
