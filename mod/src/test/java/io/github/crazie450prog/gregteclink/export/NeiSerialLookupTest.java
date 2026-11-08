package io.github.crazie450prog.gregteclink.export;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ForkJoinPool;

import org.junit.jupiter.api.AfterAll;
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

    /** Stands in for NEI's own pool ({@code ItemList.forkJoinPool}). */
    private static final ForkJoinPool POOL = new ForkJoinPool(2);

    private ArrayList<String> parallel;
    private ArrayList<String> serial;

    @AfterAll
    static void stopPool() {
        POOL.shutdown();
    }

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

    /** NEI 2.8.172 reads both fields when the lookup runs: every handler then loads on the calling thread. */
    @Test
    void aLookupThatReadsTheFieldsAtCallTimeLoadsSerially() throws Exception {
        NeiSerialLookup lookup = NeiSerialLookup.begin(field("craftinghandlers"), field("serialCraftingHandlers"));
        Map<String, String> loadedOn = neiLookup(Gui.serialCraftingHandlers, Gui.craftinghandlers);
        lookup.restore();
        assertTrue(lookup.loadedSeriallyOn(Thread.currentThread()));
        Map<String, String> want = new TreeMap<>();
        for (String h : Arrays
            .asList("bloodmagic.alchemy", "gt.recipe.electrolyzer", "gt.recipe.magicfuels", "crafting"))
            want.put(
                h,
                Thread.currentThread()
                    .getName());
        assertEquals(want, loadedOn);
    }

    /**
     * A lookup that took NEI's lists before the swap (as NEI's own {@code CommandRecipeId} dump thread does when it
     * starts) still loads the parallel ones on the pool: the swap went through, but NEI never read its lists.
     */
    @Test
    void aLookupThatTookTheListsBeforeTheSwapIsCaught() throws Exception {
        List<String> parallelBefore = Gui.craftinghandlers;
        List<String> serialBefore = Gui.serialCraftingHandlers;
        NeiSerialLookup lookup = NeiSerialLookup.begin(field("craftinghandlers"), field("serialCraftingHandlers"));
        Map<String, String> loadedOn = neiLookup(serialBefore, parallelBefore);
        lookup.restore();
        assertFalse(lookup.loadedSeriallyOn(Thread.currentThread()));
        for (String h : PARALLEL) assertTrue(
            loadedOn.get(h)
                .startsWith("ForkJoinPool"),
            h + " on " + loadedOn.get(h));
    }

    /**
     * Stands in for NEI's {@code RecipeHandlerQuery}: the serial list as a sequential stream on the calling thread,
     * then the parallel one as a parallel stream on the pool. Returns each handler's thread.
     */
    private static Map<String, String> neiLookup(List<String> serialList, List<String> parallelList) throws Exception {
        Map<String, String> loadedOn = new ConcurrentHashMap<>();
        serialList.stream()
            .forEach(
                h -> loadedOn.put(
                    h,
                    Thread.currentThread()
                        .getName()));
        POOL.submit(
            () -> parallelList.parallelStream()
                .forEach(
                    h -> loadedOn.put(
                        h,
                        Thread.currentThread()
                            .getName())))
            .get();
        return new TreeMap<>(loadedOn);
    }

    private static Field field(String name) throws NoSuchFieldException {
        return Gui.class.getField(name);
    }
}
