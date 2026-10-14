#!/usr/bin/env python3
"""Regenerate the patched net.minecraft.client.gui.Gui with the screen-share hooks.

Three steps, all asserted so a changed game version fails loudly instead of silently
shipping a build without the hooks:

  1. fixups so the decompiled file compiles at all
     - Vineflower names anonymous inner classes numerically (`new 1(...)`, `1.foo()`),
       which is not valid Java. Rewrite to the binary name `new Gui$1(...)`; `$` is a
       legal identifier character and the jar still supplies those inner classes.
       That alone is not enough for *construction*: javac hides an anonymous class's
       synthetic constructor parameters when reading one from a class file, so
       `new Gui$1(this, text)` cannot compile. The class is therefore inlined as a real
       nested class (ACTIVE_TEXT_VISITOR below) and the call site rewritten to use it.
     - drop the matching bogus `import net.minecraft.client.gui.Gui.1;` lines
     - drop `@Contract` annotations (org.jetbrains annotations is not on the classpath
       and the annotation is purely informational)
  2. inject the hooks
     - Gui.tick()                -> ScreenShareClient.tick(minecraft)
     - Gui.extractRenderState()  -> ScreenShareClient.extract(graphics, minecraft)
  3. append the inlined visitor class

Usage: patch_gui.py <decompiled Gui.java> <output Gui.java>
"""
import re
import sys

FQCN = "net.minecraft.client.gui.Gui"
SIMPLE = "Gui"
IMPL = "com.uzu.screenshare.ScreenShareClient"

TICK_ANCHOR = "        this.hud.tick(this.minecraft.isPaused());\n"
TICK_HOOK = TICK_ANCHOR + f"        {IMPL}.tick(this.minecraft);\n"

EXTRACT_ANCHOR = (
    "            this.hud.extractRenderState(graphics, deltaTracker);\n"
    "            profiler.pop();\n"
    "        }\n"
)
EXTRACT_HOOK = EXTRACT_ANCHOR + f"        {IMPL}.extract(graphics, this.minecraft);\n"

ANON_CALL_SITE = "new Gui$1(this, text)"
ANON_REPLACEMENT = "new ActiveTextVisitor(this, text)"

# Inlined verbatim from the game's anonymous GlyphVisitor (net.minecraft.client.gui.Gui$1),
# with the constructor rewritten to take its captures explicitly. Fully qualified names are
# used on purpose so this block does not depend on Gui.java's import list.
ACTIVE_TEXT_VISITOR = '''
    /**
     * Renders the debug markers for clickable/hoverable text areas. Inlined from the game's
     * anonymous GlyphVisitor so the class can be recompiled from source.
     */
    private static final class ActiveTextVisitor implements net.minecraft.client.gui.Font.GlyphVisitor {
        private final Gui owner;
        private final net.minecraft.client.renderer.state.gui.GuiTextRenderState text;
        private int index;

        ActiveTextVisitor(final Gui owner, final net.minecraft.client.renderer.state.gui.GuiTextRenderState text) {
            this.owner = owner;
            this.text = text;
        }

        @Override
        public void acceptGlyph(final net.minecraft.client.gui.font.TextRenderable.Styled glyph) {
            this.renderDebugMarkers(glyph, false);
        }

        @Override
        public void acceptEmptyArea(final net.minecraft.client.gui.font.EmptyArea empty) {
            this.renderDebugMarkers(empty, true);
        }

        private void renderDebugMarkers(final net.minecraft.client.gui.font.ActiveArea glyph, final boolean isEmpty) {
            int intensity = (isEmpty ? 128 : 255) - (this.index++ & 1) * 64;
            net.minecraft.network.chat.Style style = glyph.style();
            int red = style.getClickEvent() != null ? intensity : 0;
            int green = style.getHoverEvent() != null ? intensity : 0;
            int blue = red != 0 && green != 0 ? 0 : intensity;
            int color = net.minecraft.util.ARGB.color(128, red, green, blue);
            this.owner.guiRenderState.addGuiElement(
                new net.minecraft.client.renderer.state.gui.ColoredRectangleRenderState(
                    net.minecraft.client.renderer.RenderPipelines.GUI,
                    net.minecraft.client.gui.render.TextureSetup.noTexture(),
                    this.text.pose,
                    (int) glyph.activeLeft(),
                    (int) glyph.activeTop(),
                    (int) glyph.activeRight(),
                    (int) glyph.activeBottom(),
                    color,
                    color,
                    this.text.scissor));
        }
    }
'''


def fixups(source: str) -> str:
    """Make Vineflower's output valid Java again."""
    # `new 3("...")` -> `new Gui$3("...")` (and any other numeric reference)
    source = re.sub(r"\bnew (\d+)\(", rf"new {SIMPLE}$\1(", source)
    source = re.sub(r"(^|[^A-Za-z0-9_.$])(\d+)\.([A-Za-z_][A-Za-z0-9_]*)\(",
                    rf"\1{SIMPLE}$\2.\3(", source, flags=re.MULTILINE)
    # imports of classes whose names are not identifiers
    source = re.sub(rf"^import {re.escape(FQCN)}\.\d+;\n", "", source, flags=re.MULTILINE)
    # annotations whose jar is not on the classpath
    source = re.sub(r"^import org\.jetbrains\.annotations\.\w+;\n", "", source, flags=re.MULTILINE)
    source = re.sub(r"^ *@Contract\([^)]*\)\n", "", source, flags=re.MULTILINE)
    # anonymous class construction -> the inlined nested class
    if source.count(ANON_CALL_SITE) != 1:
        raise SystemExit(f"expected exactly one {ANON_CALL_SITE!r}, found "
                         f"{source.count(ANON_CALL_SITE)} - the game's {FQCN} changed")
    source = source.replace(ANON_CALL_SITE, ANON_REPLACEMENT, 1)
    return source


def inject(source: str) -> str:
    """Add the two hooks, asserting each anchor appears exactly once."""
    for name, anchor, hook in (("tick", TICK_ANCHOR, TICK_HOOK),
                               ("extractRenderState", EXTRACT_ANCHOR, EXTRACT_HOOK)):
        count = source.count(anchor)
        if count != 1:
            raise SystemExit(f"anchor for {name} found {count} times (expected 1) - "
                             f"the game's {FQCN} changed; re-check the hook points")
        source = source.replace(anchor, hook, 1)
    return source


def append_visitor(source: str) -> str:
    """Append the inlined visitor just before the class's closing brace."""
    end = source.rstrip().rfind("}")
    if end < 0:
        raise SystemExit("could not find the end of the class")
    return source[:end] + ACTIVE_TEXT_VISITOR + source[end:]


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    text = open(src).read()
    if IMPL in text:
        print(f"{src} is already patched (skipping)")
        return 0
    text = append_visitor(inject(fixups(text)))
    with open(dst, "w") as fh:
        fh.write(text)
    print(f"wrote {dst}")
    print(f"  hooks: {IMPL}.tick() + {IMPL}.extract()")
    print(f"  inlined: ActiveTextVisitor (was Gui$1)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
