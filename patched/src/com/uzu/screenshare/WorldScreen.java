package com.uzu.screenshare;

import com.mojang.blaze3d.vertex.PoseStack;
import com.mojang.blaze3d.vertex.VertexConsumer;
import net.minecraft.client.renderer.rendertype.RenderType;
import net.minecraft.client.renderer.rendertype.RenderTypes;
import org.joml.Vector3f;

/**
 * A screen placed in the world: a textured quad you can walk around and view from any angle,
 * like a painting or a picture frame.
 *
 * The front face points along {@code (yaw, pitch)} in Minecraft's rotation convention; a second
 * quad with reversed winding is emitted for the back so the screen is readable from behind too.
 * Coordinates are absolute world coordinates - the caller subtracts the camera position.
 */
final class WorldScreen {
    String sourceId;
    String dimension = "";
    double x;
    double y;
    double z;
    float yaw;
    float pitch;
    float width = 3.0F;      // in blocks
    float height = 1.6875F;  // 16:9
    boolean visible = true;

    ScreenSurface surface;
    private RenderType renderType;
    private String renderTypeFor;

    WorldScreen(String sourceId) {
        this.sourceId = sourceId;
    }

    /** Render type for the current texture identifier, cached (RenderTypes memoizes per id too). */
    RenderType renderType() {
        if (this.surface == null || this.surface.textureId == null) {
            return null;
        }
        String key = this.surface.textureId.toString();
        if (this.renderType == null || !key.equals(this.renderTypeFor)) {
            this.renderType = RenderTypes.entityCutout(this.surface.textureId);
            this.renderTypeFor = key;
        }
        return this.renderType;
    }

    Vector3f rightVector() {
        return new Vector3f(1.0F, 0.0F, 0.0F)
                .rotateY((float) Math.toRadians(-this.yaw))
                .rotateX((float) Math.toRadians(this.pitch));
    }

    Vector3f upVector() {
        return new Vector3f(0.0F, 1.0F, 0.0F)
                .rotateY((float) Math.toRadians(-this.yaw))
                .rotateX((float) Math.toRadians(this.pitch));
    }

    /** Aspect-corrected resize: the panel keeps the shape of the captured frame. */
    void resizeBy(float delta) {
        float aspect = this.surface != null && this.surface.frameWidth > 0
                ? (float) this.surface.frameHeight / this.surface.frameWidth
                : 9.0F / 16.0F;
        this.width = Math.max(0.25F, this.width + delta);
        this.height = Math.max(0.25F, this.width * aspect);
    }

    /** Keep the angles in a sane range so the saved layout stays readable. */
    void normalizeAngles() {
        this.yaw = ((this.yaw + 180.0F) % 360.0F + 360.0F) % 360.0F - 180.0F;
        this.pitch = Math.max(-90.0F, Math.min(90.0F, this.pitch));
    }

    /** Emit both faces of the quad at the pose's current transform. */
    void emit(VertexConsumer consumer, PoseStack.Pose pose) {
        float halfWidth = this.width / 2.0F;
        float halfHeight = this.height / 2.0F;
        // corners in CCW order as seen from the front (+Z): top-left, top-right, bottom-right, bottom-left
        float[][] corners = {
                {-halfWidth, halfHeight}, {halfWidth, halfHeight},
                {halfWidth, -halfHeight}, {-halfWidth, -halfHeight}};
        float[][] uvs = {{0.0F, 0.0F}, {1.0F, 0.0F}, {1.0F, 1.0F}, {0.0F, 1.0F}};
        emitFace(consumer, pose, corners, uvs, 1.0F, false);
        emitFace(consumer, pose, corners, uvs, -1.0F, true);
    }

    private void emitFace(VertexConsumer consumer, PoseStack.Pose pose, float[][] corners,
                          float[][] uvs, float normalZ, boolean reverse) {
        for (int i = 0; i < 4; i++) {
            int index = reverse ? 3 - i : i;
            consumer.addVertex(pose, corners[index][0], corners[index][1], 0.0F)
                    .setColor(255, 255, 255, 255)
                    .setUv(uvs[index][0], uvs[index][1])
                    // NO_OVERLAY, not 0: the overlay texture's first texel is the red damage flash,
                    // so a zeroed overlay coordinate tints the whole screen red.
                    .setOverlay(net.minecraft.client.renderer.texture.OverlayTexture.NO_OVERLAY)
                    .setLight(0xF000F0) // full brightness: a screen emits its own light
                    .setNormal(0.0F, 0.0F, normalZ);
        }
    }
}
