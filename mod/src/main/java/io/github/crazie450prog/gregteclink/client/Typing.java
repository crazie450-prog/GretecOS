package io.github.crazie450prog.gregteclink.client;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

import net.minecraft.client.gui.GuiChat;
import net.minecraft.client.gui.GuiScreenBook;
import net.minecraft.client.gui.GuiTextField;
import net.minecraft.client.gui.inventory.GuiEditSign;

/**
 * Whether the player is typing into the open screen, so a What's this? key bound to a letter doesn't fire on every
 * keystroke (review MEDIUM-2, D285): chat, a sign, a book, a focused {@link GuiTextField} the screen holds (an anvil,
 * an AE2 terminal's search box, ...), or NEI's input focus (its search box). Read by reflection, so it needs no
 * names: fields are found by their type.
 */
final class Typing {

    private static final Map<Class<?>, List<Field>> FIELDS = new ConcurrentHashMap<>();
    private static Method neiInputFocused;
    private static boolean neiLooked;

    private Typing() {}

    static boolean typing(Object screen) {
        if (screen == null) return false;
        if (screen instanceof GuiChat || screen instanceof GuiEditSign || screen instanceof GuiScreenBook) return true;
        if (neiInputFocused()) return true;
        for (Field f : textFields(screen.getClass())) {
            try {
                Object v = f.get(screen);
                if (v instanceof GuiTextField && ((GuiTextField) v).isFocused()) return true;
            } catch (ReflectiveOperationException | RuntimeException e) {
                // unreadable: not a text field we can see
            }
        }
        return false;
    }

    /** The instance fields of {@code c} and its superclasses whose type is a {@link GuiTextField}. */
    static List<Field> textFields(Class<?> c) {
        return FIELDS.computeIfAbsent(c, k -> {
            List<Field> out = new ArrayList<>();
            for (Class<?> at = k; at != null && at != Object.class; at = at.getSuperclass()) {
                for (Field f : at.getDeclaredFields()) {
                    if (Modifier.isStatic(f.getModifiers()) || !GuiTextField.class.isAssignableFrom(f.getType()))
                        continue;
                    try {
                        f.setAccessible(true);
                        out.add(f);
                    } catch (RuntimeException e) {
                        // a module wall: skip it
                    }
                }
            }
            return out;
        });
    }

    private static boolean neiInputFocused() {
        if (!neiLooked) {
            neiLooked = true;
            try {
                neiInputFocused = Class.forName("codechicken.nei.LayoutManager")
                    .getMethod("getInputFocused");
            } catch (ReflectiveOperationException | LinkageError e) {
                neiInputFocused = null; // no NEI
            }
        }
        if (neiInputFocused == null) return false;
        try {
            return neiInputFocused.invoke(null) != null;
        } catch (ReflectiveOperationException | RuntimeException | LinkageError e) {
            return false;
        }
    }
}
