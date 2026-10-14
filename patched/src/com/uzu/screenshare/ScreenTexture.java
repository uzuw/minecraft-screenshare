package com.uzu.screenshare;

import com.mojang.blaze3d.systems.RenderSystem;
import com.mojang.blaze3d.textures.FilterMode;
import net.minecraft.client.renderer.texture.DynamicTexture;

/**
 * A {@link DynamicTexture} that samples smoothly.
 *
 * DynamicTexture hardcodes a NEAREST repeat sampler, which makes a screen share look
 * pixelated as soon as the panel is not exactly the source resolution. The sampler
 * field is protected, so we simply replace it after construction.
 */
public final class ScreenTexture extends DynamicTexture {
    public ScreenTexture(String label, int width, int height) {
        super(label, width, height, false);
        this.sampler = RenderSystem.getSamplerCache().getClampToEdge(FilterMode.LINEAR);
    }
}
