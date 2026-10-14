package com.uzu.screenshare;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Reads frames published by the capture service (see screenshare/frame_format.py).
 *
 * File layout: magic "MCSF", version, format, width(u16), height(u16), seq(u32),
 * timestamp(u64), then RGB24 or RGBA8 pixels. Frames are published atomically
 * (temp file + rename), so a read either sees a whole frame or the previous one.
 */
public final class FrameReader {
    public static final String DEFAULT_DIR = "/dev/shm/mcscreen";
    public static final String MANIFEST_NAME = "sources.json";

    private static final int MAGIC = 0x4653434D; // "MCSF" little-endian
    private static final int HEADER_SIZE = 22;
    private static final int FORMAT_RGB24 = 1;

    private FrameReader() {
    }

    /** One decoded frame. {@code argb} is 0xAARRGGBB, row-major, top-left origin. */
    public record Frame(int width, int height, int seq, long timestampMs, int[] argb) {
    }

    public static Path framePath(Path dir, String sourceId) {
        return dir.resolve(sourceId + ".frame");
    }

    public static Path manifestPath(Path dir) {
        return dir.resolve(MANIFEST_NAME);
    }

    /**
     * Decode one published frame, or null when it is missing, malformed, or being replaced.
     * Called off the render thread: this does the file IO and the byte-to-ARGB conversion.
     */
    public static Frame read(Path file) throws IOException {
        byte[] data = Files.readAllBytes(file);
        if (data.length < HEADER_SIZE) {
            return null;
        }
        ByteBuffer buf = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN);
        if (buf.getInt(0) != MAGIC) {
            return null;
        }
        int version = buf.get(4) & 0xFF;
        int format = buf.get(5) & 0xFF;
        int width = buf.getShort(6) & 0xFFFF;
        int height = buf.getShort(8) & 0xFFFF;
        int seq = buf.getInt(10);
        long timestamp = buf.getLong(14);
        if (version != 1 || width <= 0 || height <= 0) {
            return null;
        }
        int channels = format == FORMAT_RGB24 ? 3 : 4;
        if (data.length < HEADER_SIZE + (long) width * height * channels) {
            return null;
        }

        int[] argb = new int[width * height];
        int offset = HEADER_SIZE;
        if (channels == 3) {
            for (int i = 0; i < argb.length; i++) {
                int r = data[offset] & 0xFF;
                int g = data[offset + 1] & 0xFF;
                int b = data[offset + 2] & 0xFF;
                offset += 3;
                argb[i] = 0xFF000000 | (r << 16) | (g << 8) | b;
            }
        } else {
            for (int i = 0; i < argb.length; i++) {
                int r = data[offset] & 0xFF;
                int g = data[offset + 1] & 0xFF;
                int b = data[offset + 2] & 0xFF;
                int a = data[offset + 3] & 0xFF;
                offset += 4;
                argb[i] = (a << 24) | (r << 16) | (g << 8) | b;
            }
        }
        return new Frame(width, height, seq, timestamp, argb);
    }
}
