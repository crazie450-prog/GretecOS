package io.github.crazie450prog.gregteclink.export;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Iterator;
import java.util.List;
import java.util.Set;
import java.util.Spliterator;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Consumer;

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
 * {@link #restore} puts both original lists back, never changed. The two lists of the call note the threads that
 * read them, so {@link #loadedSeriallyOn} can tell whether NEI really took its handlers from them.
 */
final class NeiSerialLookup {

    static final String CRAFTING_CLASS = "codechicken.nei.recipe.GuiCraftingRecipe";

    private final Field parallel;
    private final Field serial;
    private final Object parallelList;
    private final Object serialList;
    /** The lists in NEI's fields during the call. */
    private final Recording parallelNow;
    private final Recording serialNow;
    /** How many handlers the call loads serially (registered ones, hidden and empty ones included). */
    final int handlers;

    private NeiSerialLookup(Field parallel, Field serial, Object parallelList, Object serialList, Recording parallelNow,
        Recording serialNow) {
        this.parallel = parallel;
        this.serial = serial;
        this.parallelList = parallelList;
        this.serialList = serialList;
        this.parallelNow = parallelNow;
        this.serialNow = serialNow;
        this.handlers = serialNow.size();
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
        Recording all = new Recording(s);
        all.addAll(p);
        Recording none = new Recording(Collections.emptyList());
        serial.set(null, all);
        try {
            parallel.set(null, none);
        } catch (IllegalAccessException | RuntimeException e) {
            serial.set(null, s);
            throw e;
        }
        return new NeiSerialLookup(parallel, serial, p, s, none, all);
    }

    /** Puts NEI's original lists back. */
    void restore() throws IllegalAccessException {
        parallel.set(null, parallelList);
        serial.set(null, serialList);
    }

    /**
     * Whether NEI's lookup took its handlers from the call's two lists: it read the serial list on {@code caller},
     * which NEI 2.8.172 streams one handler after another on the calling thread, and it read the empty parallel list.
     * False otherwise: a lookup that took NEI's lists before {@link #begin}, for example, reads neither and still loads
     * the handlers in parallel.
     */
    boolean loadedSeriallyOn(Thread caller) {
        return serialNow.readers.contains(caller) && !parallelNow.readers.isEmpty();
    }

    /** The threads that read each list of the call, by name, for the log. */
    String readers() {
        return "serial list read on " + names(serialNow) + ", parallel list on " + names(parallelNow);
    }

    private static List<String> names(Recording list) {
        List<String> out = new ArrayList<>();
        for (Thread t : list.readers) out.add(t.getName());
        return out;
    }

    /**
     * A list that notes every thread that streams or iterates it ({@code stream()} and {@code parallelStream()} both
     * call {@link #spliterator}).
     */
    static final class Recording extends ArrayList<Object> {

        final Set<Thread> readers = ConcurrentHashMap.newKeySet();

        Recording(Collection<?> c) {
            super(c);
        }

        private void read() {
            readers.add(Thread.currentThread());
        }

        @Override
        public Spliterator<Object> spliterator() {
            read();
            return super.spliterator();
        }

        @Override
        public Iterator<Object> iterator() {
            read();
            return super.iterator();
        }

        @Override
        public void forEach(Consumer<? super Object> action) {
            read();
            super.forEach(action);
        }
    }
}
