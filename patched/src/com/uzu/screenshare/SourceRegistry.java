package com.uzu.screenshare;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.mojang.logging.LogUtils;
import net.minecraft.client.Minecraft;
import net.minecraft.resources.Identifier;
import org.slf4j.Logger;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Discovers capture sources and keeps one {@link ScreenSurface} (live texture) per source.
 *
 * The capture service writes /dev/shm/mcscreen/sources.json plus one .frame file per source;
 * this polls that manifest, and schedules frame reads on a single reader thread so the render
 * thread only ever does the texture upload.
 */
final class SourceRegistry {
    private static final Logger LOGGER = LogUtils.getLogger();
    private static final Path DIR = Paths.get(FrameReader.DEFAULT_DIR);
    private static final long MANIFEST_INTERVAL_MS = 1000L;
    private static final long FRAME_INTERVAL_MS = 66L; // ~15 fps per surface

    private static final Map<String, ScreenSurface> SURFACES = new LinkedHashMap<>();
    private static ExecutorService reader;
    private static long nextManifestAt;
    private static boolean warnedMissingDir;

    private SourceRegistry() {
    }

    static List<ScreenSurface> all() {
        return new ArrayList<>(SURFACES.values());
    }

    static ScreenSurface get(String sourceId) {
        return SURFACES.get(sourceId);
    }

    static int size() {
        return SURFACES.size();
    }

    /** Poll the manifest (1 Hz) and start a frame reader thread on first use. */
    static void poll(Minecraft minecraft, long now) {
        if (reader == null) {
            reader = Executors.newSingleThreadExecutor(runnable -> {
                Thread thread = new Thread(runnable, "uzu-screenshare-reader");
                thread.setDaemon(true);
                return thread;
            });
        }
        if (now < nextManifestAt) {
            return;
        }
        nextManifestAt = now + MANIFEST_INTERVAL_MS;
        if (!Files.isDirectory(DIR)) {
            if (!warnedMissingDir) {
                warnedMissingDir = true;
                LOGGER.warn("[screenshare] {} does not exist - start screenshare/capture_service.py", DIR);
            }
            return;
        }
        JsonObject root = readManifest();
        if (root == null) {
            return;
        }
        JsonArray sources = root.getAsJsonArray("sources");
        if (sources == null) {
            return;
        }
        List<String> present = new ArrayList<>();
        for (JsonElement element : sources) {
            JsonObject source = element.getAsJsonObject();
            if (!source.has("id")) {
                continue;
            }
            String id = source.get("id").getAsString();
            present.add(id);
            ScreenSurface surface = SURFACES.get(id);
            if (surface == null) {
                surface = new ScreenSurface(id);
                SURFACES.put(id, surface);
                LOGGER.info("[screenshare] source {} ({}) available", id, string(source, "detail"));
            }
            surface.kind = string(source, "kind");
            surface.name = string(source, "name");
            surface.detail = string(source, "detail");
            if (source.has("fps")) {
                surface.fps = (int) Math.round(source.get("fps").getAsDouble());
            }
        }
        List<String> gone = new ArrayList<>();
        for (String id : SURFACES.keySet()) {
            if (!present.contains(id)) {
                gone.add(id);
            }
        }
        for (String id : gone) {
            SURFACES.remove(id);
            LOGGER.info("[screenshare] source {} is gone", id);
        }
    }

    /** Queue a read for every surface a consumer wants, at most every FRAME_INTERVAL_MS. */
    static void scheduleReads(long now) {
        if (reader == null) {
            return;
        }
        for (ScreenSurface surface : SURFACES.values()) {
            if (!surface.wanted || now < surface.nextReadAt || !surface.beginRead()) {
                continue;
            }
            surface.nextReadAt = now + FRAME_INTERVAL_MS;
            reader.submit(() -> {
                try {
                    surface.stage(FrameReader.read(FrameReader.framePath(DIR, surface.sourceId)));
                } catch (IOException e) {
                    // The publisher replaced the file between reads: try again next cycle.
                } finally {
                    surface.endRead();
                }
            });
        }
    }

    /** Create and register the GPU texture for a surface (render thread only). */
    static void ensureTexture(Minecraft minecraft, ScreenSurface surface) {
        if (surface.texture != null) {
            return;
        }
        int width = Math.max(1, surface.stagedWidth());
        int height = Math.max(1, surface.stagedHeight());
        surface.texture = new ScreenTexture("uzu-screenshare/" + surface.sourceId, width, height);
        surface.frameWidth = width;
        surface.frameHeight = height;
        surface.textureId = Identifier.fromNamespaceAndPath("uzu_screenshare", sanitize(surface.sourceId));
        minecraft.getTextureManager().register(surface.textureId, surface.texture);
    }

    static void releaseTexture(Minecraft minecraft, ScreenSurface surface) {
        if (surface.texture == null) {
            return;
        }
        if (surface.textureId != null) {
            minecraft.getTextureManager().release(surface.textureId);
            surface.textureId = null;
        }
        surface.texture.close();
        surface.texture = null;
        surface.lastSeq = -1;
    }

    private static JsonObject readManifest() {
        try {
            return JsonParser.parseString(Files.readString(FrameReader.manifestPath(DIR)))
                    .getAsJsonObject();
        } catch (IOException | RuntimeException e) {
            return null;
        }
    }

    private static String string(JsonObject object, String key) {
        return object.has(key) && !object.get(key).isJsonNull() ? object.get(key).getAsString() : "";
    }

    private static String sanitize(String id) {
        StringBuilder out = new StringBuilder(id.length());
        for (char c : id.toLowerCase(java.util.Locale.ROOT).toCharArray()) {
            out.append((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9')
                    || c == '-' || c == '_' || c == '.' || c == '/' ? c : '_');
        }
        return out.toString();
    }
}
