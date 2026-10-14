package com.uzu.screenshare;

import com.mojang.blaze3d.platform.InputConstants;
import com.mojang.blaze3d.platform.Window;

import java.util.HashSet;
import java.util.Set;

/**
 * Key polling with edge detection.
 *
 * The game exposes no key-listener hook to a patched client without a mod loader, so keys are
 * polled once per client tick. That means a press must last long enough to be seen (humans are
 * fine; scripted input must hold each key rather than sending an instant press/release).
 */
final class Keys {
    private static final Set<Integer> HELD = new HashSet<>();

    private Keys() {
    }

    /** True exactly once per press. */
    static boolean pressed(Window window, int key) {
        boolean down = InputConstants.isKeyDown(window, key);
        boolean fresh = down && HELD.add(key);
        if (!down) {
            HELD.remove(key);
        }
        return fresh;
    }

    /** True while the key is held (for smooth movement). */
    static boolean held(Window window, int key) {
        return InputConstants.isKeyDown(window, key);
    }

    /** Every key code currently down, for the debug dump. */
    static String downCodes(Window window) {
        StringBuilder out = new StringBuilder();
        for (int code = 32; code <= 348; code++) {
            if (InputConstants.isKeyDown(window, code)) {
                out.append(code).append(' ');
            }
        }
        return out.toString().trim();
    }

    static final int PLUS = InputConstants.getKey("key.keyboard.equal").getValue();
    static final int MINUS = InputConstants.getKey("key.keyboard.minus").getValue();
}
