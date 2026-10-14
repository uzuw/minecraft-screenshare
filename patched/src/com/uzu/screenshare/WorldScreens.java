package com.uzu.screenshare;

import com.mojang.blaze3d.platform.InputConstants;
import com.mojang.blaze3d.platform.Window;
import com.mojang.blaze3d.vertex.PoseStack;
import com.mojang.logging.LogUtils;
import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.SubmitNodeCollector;
import net.minecraft.client.renderer.state.level.CameraRenderState;
import net.minecraft.client.renderer.state.level.LevelRenderState;
import net.minecraft.core.Direction;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;
import org.joml.Vector3f;
import org.slf4j.Logger;

import java.util.ArrayList;
import java.util.List;

/**
 * The in-world screens: placement, editing, persistence, and the world-space draw call.
 *
 * {@link #submit} is called once per frame from the patched LevelRenderer, right after the
 * world has collected its own geometry, so our quads ride the normal world pipeline (depth
 * tested, fogged, occluded by blocks) instead of being pasted over the HUD.
 *
 * Controls: F7 places a screen where you are looking (bound to the next free capture source);
 * F8 toggles edit mode, where TAB selects a screen, arrows move it, +/- resize it, [ ] rotate
 * it, ; ' tilt it, N cycles its source, DEL removes it and R resets the layout.
 */
public final class WorldScreens {
    private static final Logger LOGGER = LogUtils.getLogger();
    private static final List<WorldScreen> SCREENS = new ArrayList<>();
    private static final float MOVE_STEP = 0.25F;
    private static final float RESIZE_STEP = 0.25F;
    private static final float ROTATE_STEP = 15.0F;
    private static final double PLACE_DISTANCE = 4.0D;
    // InputConstants has no constants for these; look them up by their translation key.
    private static final int KEY_LEFT_BRACKET =
            InputConstants.getKey("key.keyboard.left.bracket").getValue();
    private static final int KEY_RIGHT_BRACKET =
            InputConstants.getKey("key.keyboard.right.bracket").getValue();

    private static String selected;
    private static boolean editMode;
    private static boolean dirty;
    private static boolean loaded;
    private static long nextSaveAt;
    private static long nextStatusAt;

    private WorldScreens() {
    }

    static List<WorldScreen> all() {
        return SCREENS;
    }

    static boolean editMode() {
        return editMode;
    }

    /** Called from Gui.tick (via ScreenShareClient) once per client tick. */
    static void tick(Minecraft minecraft) {
        if (!loaded) {
            loaded = true;
            LayoutStore.loadWorldScreens(minecraft.gameDirectory.toPath(), SCREENS);
            if (!SCREENS.isEmpty()) {
                LOGGER.info("[screenshare] loaded {} in-world screen(s)", SCREENS.size());
            }
        }
        if (minecraft.level == null || minecraft.player == null) {
            return;
        }
        String dimension = minecraft.level.dimension().identifier().toString();
        for (WorldScreen screen : SCREENS) {
            ScreenSurface surface = SourceRegistry.get(screen.sourceId);
            screen.surface = surface;
            if (surface != null && screen.visible && dimension.equals(screen.dimension)) {
                surface.wanted = true;
            }
        }
        handleKeys(minecraft);
        long now = System.currentTimeMillis();
        if (dirty && now >= nextSaveAt) {
            dirty = false;
            nextSaveAt = now + 2000L;
            LayoutStore.saveWorldScreens(minecraft.gameDirectory.toPath(), SCREENS);
        }
        if (editMode && now >= nextStatusAt) {
            nextStatusAt = now + 1000L;
            WorldScreen screen = selectedScreen();
            if (screen != null) {
                LOGGER.info("[screenshare] edit {} {}x{} at {},{},{} yaw {} pitch {}",
                        screen.sourceId, round(screen.width), round(screen.height),
                        round(screen.x), round(screen.y), round(screen.z),
                        round(screen.yaw), round(screen.pitch));
            }
        }
    }

    /** Called from the patched LevelRenderer.render, after the world's own geometry is collected. */
    public static void submit(SubmitNodeCollector collector, LevelRenderState levelRenderState,
                              Minecraft minecraft) {
        if (SCREENS.isEmpty() || minecraft.level == null) {
            return;
        }
        CameraRenderState camera = levelRenderState.cameraRenderState;
        if (camera == null || camera.pos == null) {
            return;
        }
        Vec3 cameraPos = camera.pos;
        String dimension = minecraft.level.dimension().identifier().toString();
        for (WorldScreen screen : SCREENS) {
            if (!screen.visible || !dimension.equals(screen.dimension)) {
                continue;
            }
            ScreenSurface surface = screen.surface != null ? screen.surface
                    : SourceRegistry.get(screen.sourceId);
            screen.surface = surface;
            if (surface == null) {
                continue;
            }
            // Upload any freshly staged frame here: this is the render thread.
            if (surface.hasPending()) {
                if (surface.needsResize() && surface.texture != null) {
                    SourceRegistry.releaseTexture(minecraft, surface);
                }
                SourceRegistry.ensureTexture(minecraft, surface);
                surface.pushToTexture();
            }
            if (surface.textureId == null) {
                continue;
            }
            var renderType = screen.renderType();
            if (renderType == null) {
                continue;
            }
            PoseStack pose = new PoseStack();
            pose.translate(screen.x - cameraPos.x, screen.y - cameraPos.y, screen.z - cameraPos.z);
            pose.mulPose(com.mojang.math.Axis.YP.rotationDegrees(-screen.yaw));
            pose.mulPose(com.mojang.math.Axis.XP.rotationDegrees(screen.pitch));
            collector.submitCustomGeometry(pose, renderType,
                    (poseEntry, consumer) -> screen.emit(consumer, poseEntry));
        }
    }

    // ------------------------------------------------------------------ placement / editing

    private static void handleKeys(Minecraft minecraft) {
        Window window = minecraft.getWindow();
        if (Keys.pressed(window, InputConstants.KEY_F7)) {
            place(minecraft);
        }
        if (Keys.pressed(window, InputConstants.KEY_F8)) {
            editMode = !editMode;
            LOGGER.info("[screenshare] screen edit mode {}", editMode ? "on" : "off");
        }
        if (!editMode) {
            return;
        }
        if (SCREENS.isEmpty()) {
            return;
        }
        if (selected == null || selectedScreen() == null) {
            selected = SCREENS.get(0).sourceId;
        }
        if (Keys.pressed(window, InputConstants.KEY_TAB)) {
            int index = indexOfSelected();
            selected = SCREENS.get((index + 1) % SCREENS.size()).sourceId;
        }
        WorldScreen screen = selectedScreen();
        if (screen == null) {
            return;
        }
        boolean changed = false;
        Vector3f right = screen.rightVector().mul(MOVE_STEP);
        Vector3f up = screen.upVector().mul(MOVE_STEP);
        if (Keys.held(window, InputConstants.KEY_RIGHT)) {
            screen.x += right.x;
            screen.y += right.y;
            screen.z += right.z;
            changed = true;
        }
        if (Keys.held(window, InputConstants.KEY_LEFT)) {
            screen.x -= right.x;
            screen.y -= right.y;
            screen.z -= right.z;
            changed = true;
        }
        if (Keys.held(window, InputConstants.KEY_UP)) {
            screen.x += up.x;
            screen.y += up.y;
            screen.z += up.z;
            changed = true;
        }
        if (Keys.held(window, InputConstants.KEY_DOWN)) {
            screen.x -= up.x;
            screen.y -= up.y;
            screen.z -= up.z;
            changed = true;
        }
        if (Keys.pressed(window, Keys.PLUS) || Keys.pressed(window, InputConstants.KEY_ADD)) {
            screen.resizeBy(RESIZE_STEP);
            changed = true;
        }
        if (Keys.pressed(window, Keys.MINUS) || Keys.pressed(window, InputConstants.KEY_MINUS)) {
            screen.resizeBy(-RESIZE_STEP);
            changed = true;
        }
        if (Keys.pressed(window, KEY_LEFT_BRACKET)) {
            screen.yaw -= ROTATE_STEP;
            changed = true;
        }
        if (Keys.pressed(window, KEY_RIGHT_BRACKET)) {
            screen.yaw += ROTATE_STEP;
            changed = true;
        }
        if (Keys.pressed(window, InputConstants.KEY_SEMICOLON)) {
            screen.pitch = Math.max(-90.0F, screen.pitch - ROTATE_STEP);
            changed = true;
        }
        if (Keys.pressed(window, InputConstants.KEY_APOSTROPHE)) {
            screen.pitch = Math.min(90.0F, screen.pitch + ROTATE_STEP);
            changed = true;
        }
        if (Keys.pressed(window, InputConstants.KEY_N)) {
            cycleSource(screen);
            changed = true;
        }
        if (Keys.pressed(window, InputConstants.KEY_DELETE)) {
            SCREENS.remove(screen);
            selected = SCREENS.isEmpty() ? null : SCREENS.get(0).sourceId;
            changed = true;
            LOGGER.info("[screenshare] screen removed ({} left)", SCREENS.size());
        }
        if (Keys.pressed(window, InputConstants.KEY_R)) {
            for (int i = 0; i < SCREENS.size(); i++) {
                reposition(minecraft, SCREENS.get(i), i);
            }
            changed = true;
        }
        if (changed) {
            dirty = true;
        }
    }

    /** Place a new screen where the player is looking, bound to the next unused source. */
    private static void place(Minecraft minecraft) {
        List<ScreenSurface> surfaces = SourceRegistry.all();
        if (surfaces.isEmpty()) {
            LOGGER.warn("[screenshare] no capture sources available (is capture_service.py running?)");
            return;
        }
        ScreenSurface surface = null;
        for (ScreenSurface candidate : surfaces) {
            boolean used = false;
            for (WorldScreen existing : SCREENS) {
                if (existing.sourceId.equals(candidate.sourceId)) {
                    used = true;
                    break;
                }
            }
            if (!used) {
                surface = candidate;
                break;
            }
        }
        if (surface == null) {
            LOGGER.info("[screenshare] all {} source(s) are already placed", surfaces.size());
            return;
        }
        String dimension = minecraft.level.dimension().identifier().toString();
        Vec3 position = placementPosition(minecraft);
        WorldScreen screen = new WorldScreen(surface.sourceId);
        screen.dimension = dimension;
        screen.x = position.x;
        screen.y = position.y;
        screen.z = position.z;
        // Face back towards the player: the normal is the reverse of the player's look vector.
        screen.yaw = minecraft.player.getYRot() + 180.0F;
        screen.pitch = -minecraft.player.getXRot();
        screen.surface = surface;
        SCREENS.add(screen);
        selected = screen.sourceId;
        dirty = true;
        LOGGER.info("[screenshare] placed screen for {} at {},{},{} ({} total)", surface.sourceId,
                round(position.x), round(position.y), round(position.z), SCREENS.size());
    }

    /** Where the new screen goes: on the block face being looked at, else floating in front. */
    private static Vec3 placementPosition(Minecraft minecraft) {
        HitResult hit = minecraft.hitResult;
        if (hit != null && hit.getType() == HitResult.Type.BLOCK && hit instanceof BlockHitResult block) {
            Direction face = block.getDirection();
            return hit.getLocation().add(face.getStepX() * 0.06, face.getStepY() * 0.06,
                    face.getStepZ() * 0.06);
        }
        Vec3 eye = minecraft.player.getEyePosition();
        return eye.add(minecraft.player.getLookAngle().scale(PLACE_DISTANCE));
    }

    private static void reposition(Minecraft minecraft, WorldScreen screen, int index) {
        Vec3 base = minecraft.player != null ? minecraft.player.position() : Vec3.ZERO;
        screen.x = base.x + (index % 3) * 4.0D;
        screen.y = base.y + 1.5D + (index / 3) * 2.5D;
        screen.z = base.z + 4.0D;
        screen.yaw = 180.0F;
        screen.pitch = 0.0F;
        screen.visible = true;
    }

    private static void cycleSource(WorldScreen screen) {
        List<ScreenSurface> surfaces = SourceRegistry.all();
        if (surfaces.isEmpty()) {
            return;
        }
        int current = -1;
        for (int i = 0; i < surfaces.size(); i++) {
            if (surfaces.get(i).sourceId.equals(screen.sourceId)) {
                current = i;
            }
        }
        screen.sourceId = surfaces.get((current + 1) % surfaces.size()).sourceId;
        screen.surface = SourceRegistry.get(screen.sourceId);
        LOGGER.info("[screenshare] screen source -> {}", screen.sourceId);
    }

    private static WorldScreen selectedScreen() {
        for (WorldScreen screen : SCREENS) {
            if (screen.sourceId.equals(selected)) {
                return screen;
            }
        }
        return null;
    }

    private static int indexOfSelected() {
        for (int i = 0; i < SCREENS.size(); i++) {
            if (SCREENS.get(i).sourceId.equals(selected)) {
                return i;
            }
        }
        return 0;
    }

    private static String round(double value) {
        return String.format(java.util.Locale.ROOT, "%.2f", value);
    }
}
