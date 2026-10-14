package com.uzu.screenshare;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/** Persists the in-world screens (position, facing, size, bound source) per game directory. */
final class LayoutStore {
    private static final String FILE_NAME = "screenshare.json";

    private LayoutStore() {
    }

    static void loadWorldScreens(Path gameDirectory, List<WorldScreen> out) {
        Path file = gameDirectory.resolve(FILE_NAME);
        if (!Files.isRegularFile(file)) {
            return;
        }
        try {
            JsonObject root = JsonParser.parseString(Files.readString(file)).getAsJsonObject();
            JsonArray screens = root.has("screens") ? root.getAsJsonArray("screens") : null;
            if (screens == null) {
                return;
            }
            for (JsonElement element : screens) {
                JsonObject o = element.getAsJsonObject();
                if (!o.has("sourceId")) {
                    continue;
                }
                WorldScreen screen = new WorldScreen(o.get("sourceId").getAsString());
                screen.dimension = o.has("dimension") ? o.get("dimension").getAsString() : "";
                screen.x = o.get("x").getAsDouble();
                screen.y = o.get("y").getAsDouble();
                screen.z = o.get("z").getAsDouble();
                screen.yaw = o.get("yaw").getAsFloat();
                screen.pitch = o.has("pitch") ? o.get("pitch").getAsFloat() : 0.0F;
                screen.width = o.has("width") ? o.get("width").getAsFloat() : 3.0F;
                screen.height = o.has("height") ? o.get("height").getAsFloat() : 1.6875F;
                screen.visible = !o.has("visible") || o.get("visible").getAsBoolean();
                out.add(screen);
            }
        } catch (IOException | RuntimeException e) {
            // A corrupt layout must never stop the client.
            System.err.println("[screenshare] could not read " + file + ": " + e);
        }
    }

    static void saveWorldScreens(Path gameDirectory, List<WorldScreen> screens) {
        JsonArray array = new JsonArray();
        for (WorldScreen screen : screens) {
            screen.normalizeAngles();
            JsonObject o = new JsonObject();
            o.addProperty("sourceId", screen.sourceId);
            o.addProperty("dimension", screen.dimension);
            o.addProperty("x", screen.x);
            o.addProperty("y", screen.y);
            o.addProperty("z", screen.z);
            o.addProperty("yaw", screen.yaw);
            o.addProperty("pitch", screen.pitch);
            o.addProperty("width", screen.width);
            o.addProperty("height", screen.height);
            o.addProperty("visible", screen.visible);
            array.add(o);
        }
        JsonObject root = new JsonObject();
        root.addProperty("version", 2);
        root.add("screens", array);
        Gson gson = new GsonBuilder().setPrettyPrinting().create();
        try {
            Files.writeString(gameDirectory.resolve(FILE_NAME), gson.toJson(root));
        } catch (IOException e) {
            System.err.println("[screenshare] could not write the layout: " + e);
        }
    }
}
