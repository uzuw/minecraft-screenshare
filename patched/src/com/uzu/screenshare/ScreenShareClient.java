package com.uzu.screenshare;

import com.mojang.logging.LogUtils;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import org.slf4j.Logger;

import java.util.List;

/**
 * Entry point of the screen-share client, hooked into the game by the patched Gui:
 * {@link #tick} once per client tick, {@link #extract} once per frame.
 *
 * The screens themselves are rendered in the world (see {@link WorldScreens}, hooked into
 * LevelRenderer). This class only drives the capture registry and draws the small edit-mode
 * hint; it deliberately draws no overlay of the screen contents.
 */
public final class ScreenShareClient {
    private static final Logger LOGGER = LogUtils.getLogger();
    private static boolean announced;

    private ScreenShareClient() {
    }

    /** Called once per client tick from the patched Gui. */
    public static void tick(Minecraft minecraft) {
        long now = System.currentTimeMillis();
        SourceRegistry.poll(minecraft, now);
        WorldScreens.tick(minecraft);
        SourceRegistry.scheduleReads(now);
        if (!announced && SourceRegistry.size() > 0) {
            announced = true;
            LOGGER.info("[screenshare] {} source(s) ready - F7 places a screen where you look, "
                    + "F8 edits", SourceRegistry.size());
        }
    }

    /** Called every frame from the patched Gui, after the HUD extracted its state. */
    public static void extract(GuiGraphicsExtractor graphics, Minecraft minecraft) {
        if (!WorldScreens.editMode()) {
            return;
        }
        List<String> lines = List.of(
                "SCREEN EDIT - F8 to finish",
                "TAB select   arrows move   +/- resize   [ ] rotate   ; ' tilt   N source   DEL remove   R reset");
        int y = minecraft.getWindow().getGuiScaledHeight() - 30;
        for (String line : lines) {
            graphics.text(minecraft.font, line, 12, y, 0xFFFFF0A0);
            y += 12;
        }
        graphics.text(minecraft.font, WorldScreens.all().size() + " screen(s) in this world",
                12, y, 0xFFE0E0E0);
    }
}
