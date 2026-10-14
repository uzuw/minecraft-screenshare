package com.uzu.screenshare;

import com.mojang.blaze3d.platform.NativeImage;
import net.minecraft.resources.Identifier;

/**
 * One capture source as a live GPU texture, plus the handoff between threads.
 *
 * The reader thread decodes the published frame into {@code stagedArgb} and flips
 * {@code pending}; the render thread copies it into the texture and uploads. Consumers
 * (HUD panels, in-world screens) set {@code wanted} each frame so reads are only scheduled
 * for surfaces someone is actually showing.
 */
final class ScreenSurface {
    final String sourceId;
    String name = "";
    String kind = "";
    String detail = "";
    int fps;

    ScreenTexture texture;
    Identifier textureId;
    int frameWidth;
    int frameHeight;
    int lastSeq = -1;

    boolean wanted;
    long nextReadAt;

    private int[] stagedArgb;
    private int stagedWidth;
    private int stagedHeight;
    private int stagedSeq = -1;
    private volatile boolean pending;
    private volatile boolean reading;

    ScreenSurface(String sourceId) {
        this.sourceId = sourceId;
    }

    boolean beginRead() {
        if (this.reading) {
            return false;
        }
        this.reading = true;
        return true;
    }

    void endRead() {
        this.reading = false;
    }

    /** Called on the reader thread. */
    void stage(FrameReader.Frame frame) {
        if (frame == null || frame.seq() == this.stagedSeq) {
            return;
        }
        this.stagedArgb = frame.argb();
        this.stagedWidth = frame.width();
        this.stagedHeight = frame.height();
        this.stagedSeq = frame.seq();
        this.pending = true;
    }

    boolean hasPending() {
        return this.pending;
    }

    int stagedWidth() {
        return this.stagedWidth;
    }

    int stagedHeight() {
        return this.stagedHeight;
    }

    boolean needsResize() {
        return this.pending && (this.texture == null
                || this.frameWidth != this.stagedWidth || this.frameHeight != this.stagedHeight);
    }

    /** Called on the render thread: copy staged pixels into the texture and upload. */
    void pushToTexture() {
        int[] argb = this.stagedArgb;
        this.pending = false;
        if (argb == null || this.texture == null) {
            return;
        }
        NativeImage image = this.texture.getPixels();
        if (image.getWidth() != this.stagedWidth || image.getHeight() != this.stagedHeight) {
            return;
        }
        int index = 0;
        for (int y = 0; y < this.stagedHeight; y++) {
            for (int x = 0; x < this.stagedWidth; x++) {
                image.setPixel(x, y, argb[index++]);
            }
        }
        this.texture.upload();
        this.lastSeq = this.stagedSeq;
    }

    boolean hasFrame() {
        return this.lastSeq >= 0;
    }

    String label() {
        String state = this.lastSeq < 0 ? " (waiting)" : "";
        String rate = this.fps > 0 ? " " + this.fps + "fps" : "";
        return this.name + " - " + this.frameWidth + "x" + this.frameHeight + rate + state;
    }
}
