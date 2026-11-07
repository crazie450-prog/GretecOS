package io.github.crazie450prog.gregteclink.export;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.List;

/**
 * Makes NEI 2.8.172 load every recipe handler on the calling thread for one {@code dumpAll} call (D188). Its lookup
 * ({@code GuiCraftingRecipe.getCraftingHandlers("all")}, {@code RecipeHandlerQuery}) loads the handlers listed in
 * {@code GuiCraftingRecipe.craftinghandlers} in parallel on NEI's ForkJoin workers, and those in
 * {@code serialCraftingHandlers} one after another on the calling thread. GT 5.09.54.265's handlers are not safe to
 * load in parallel: the first one builds GT's lazy oredict table ({@code GTOreDictUnificator.getNonUnifiedStacks})
 * while the others already read it, so a handler fails (and NEI drops it) or silently loses oredict alternatives.
 * NEI's own switch, {@code config/NEI/serialhandlers.cfg}, can't reach GT's handlers: GT adds them to
 * {@code craftinghandlers} directly. So for the call every handler is in the serial list, NEI's own serial ones
 * first (NEI orders its result as serial, then parallel, before its stable sort), and the parallel list is empty;
 * {@link #restore} puts both original lists back, never changed.
 */
final class NeiSerialLookup {

    static final String CRAFTING_CLASS = "codechicken.nei.recipe.GuiCraftingRecipe";

    private final Field parallel;
    private final Field serial;
    private final Object parallelList;
    private final Object serialList;
    /** How many handlers the call loads serially (registered ones, hidden and empty ones included). */
    final int handlers;

    private NeiSerialLookup(Field parallel, Field serial, Object parallelList, Object serialList, int handlers) {
        this.parallel = parallel;
        this.serial = serial;
        this.parallelList = parallelList;
        this.serialList = serialList;
        this.handlers = handlers;
    }

    /** Moves every crafting handler of NEI's to its serial list until {@link #restore}. */
    static NeiSerialLookup begin() throws ReflectiveOperationException {
        Class<?> c = Class.forName(CRAFTING_CLASS);
        return begin(c.getField("craftinghandlers"), c.getField("serialCraftingHandlers"));
    }

    /** The same on any two static list fields: the parallel one and the serial one. */
    static NeiSerialLookup begin(Field parallel, Field serial) throws IllegalAccessException {
        List<?> p = (List<?>) parallel.get(null);
        List<?> s = (List<?>) serial.get(null);
        ArrayList<Object> all = new ArrayList<>(s);
        all.addAll(p);
        serial.set(null, all);
        try {
            parallel.set(null, new ArrayList<>());
        } catch (IllegalAccessException | RuntimeException e) {
            serial.set(null, s);
            throw e;
        }
        return new NeiSerialLookup(parallel, serial, p, s, all.size());
    }

    /** Puts NEI's original lists back. */
    void restore() throws IllegalAccessException {
        parallel.set(null, parallelList);
        serial.set(null, serialList);
    }
}
