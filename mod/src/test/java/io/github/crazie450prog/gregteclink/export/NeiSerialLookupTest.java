package io.github.crazie450prog.gregteclink.export;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/** The NEI step's serial handler lookup (D188) on two static lists shaped like NEI's {@code GuiCraftingRecipe}'s. */
class NeiSerialLookupTest {

    /** Stands in for {@code GuiCraftingRecipe}: its parallel and its serial crafting handler lists. */
    public static final class Gui {

        public static ArrayList<String> craftinghandlers;
        public static ArrayList<String> serialCraftingHandlers;
        public static final ArrayList<String> FIXED = new ArrayList<>();
    }

    private static final List<String> PARALLEL = Arrays
        .asList("gt.recipe.electrolyzer", "gt.recipe.magicfuels", "crafting");
    private static final List<String> SERIAL = Arrays.asList("bloodmagic.alchemy");

    private ArrayList<String> parallel;
    private ArrayList<String> serial;

    @BeforeEach
    void setUp() {
        parallel = new ArrayList<>(PARALLEL);
        serial = new ArrayList<>(SERIAL);
        Gui.craftinghandlers = parallel;
        Gui.serialCraftingHandlers = serial;
    }

    /** NEI orders its result serial first, then parallel, before a stable sort: the same order, so the same files. */
    @Test
    void duringTheCallEveryHandlerIsSerialInNeisOrder() throws ReflectiveOperationException {
        NeiSerialLookup lookup = NeiSerialLookup.begin(field("craftinghandlers"), field("serialCraftingHandlers"));
        assertEquals(
            Arrays.asList("bloodmagic.alchemy", "gt.recipe.electrolyzer", "gt.recipe.magicfuels", "crafting"),
            Gui.serialCraftingHandlers);
        assertTrue(Gui.craftinghandlers.isEmpty());
        assertEquals(4, lookup.handlers);
    }

    @Test
    void afterTheCallNeisOwnListsAreBackUnchanged() throws ReflectiveOperationException {
        NeiSerialLookup lookup = NeiSerialLookup.begin(field("craftinghandlers"), field("serialCraftingHandlers"));
        Gui.serialCraftingHandlers.add("added during the call");
        Gui.craftinghandlers.add("added during the call");
        lookup.restore();
        assertSame(parallel, Gui.craftinghandlers);
        assertSame(serial, Gui.serialCraftingHandlers);
        assertEquals(PARALLEL, parallel);
        assertEquals(SERIAL, serial);
    }

    /** A parallel list that can't be emptied leaves the serial one as it was, and the step fails. */
    @Test
    void aListThatCantBeReplacedChangesNothing() throws ReflectiveOperationException {
        Field fixed = field("FIXED");
        assertThrows(IllegalAccessException.class, () -> NeiSerialLookup.begin(fixed, field("serialCraftingHandlers")));
        assertSame(serial, Gui.serialCraftingHandlers);
        assertEquals(SERIAL, serial);
    }

    private static Field field(String name) throws NoSuchFieldException {
        return Gui.class.getField(name);
    }
}
