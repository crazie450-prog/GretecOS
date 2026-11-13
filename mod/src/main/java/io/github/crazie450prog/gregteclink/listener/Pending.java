package io.github.crazie450prog.gregteclink.listener;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Placements and breaks seen during a server tick, settled at the tick's end (WO-6.2, D260).
 *
 * <p>
 * Forge's {@code PlaceEvent} comes while the placement is still being captured and can still be cancelled, and
 * {@code BreakEvent} comes before the block is removed and can be cancelled by a later handler. So neither is
 * reported at once: each is noted here, and at the end of the tick {@link #settle} looks at the world as it is then. A
 * placement is a {@code machine_built} only if a GT machine stands there now; a break is a {@code machine_removed}
 * only if the machine noted at the break is no longer there. Pure logic: the world is reached through {@link World}.
 */
final class Pending {

    /** What the settle step asks of the world. Reads only. */
    interface World {

        /**
         * The GT MetaTileEntity id of the machine at the position, or 0 if there is none (or its chunk isn't loaded).
         */
        int mteAt(int dim, int x, int y, int z);

        /** The registry name of the block at the position, or null when it can't be read. */
        String blockAt(int dim, int x, int y, int z);
    }

    /** One settled event. {@code kind} is {@link #BUILT} or {@link #REMOVED}. */
    static final class Event {

        final String kind;
        final int dim;
        final int x;
        final int y;
        final int z;
        final int mteId;
        final String registry;

        Event(String kind, int dim, int x, int y, int z, int mteId, String registry) {
            this.kind = kind;
            this.dim = dim;
            this.x = x;
            this.y = y;
            this.z = z;
            this.mteId = mteId;
            this.registry = registry;
        }

        @Override
        public String toString() {
            return kind + " mte:" + mteId + " " + registry + " @" + dim + ":" + x + "," + y + "," + z;
        }
    }

    static final String BUILT = "machine_built";
    static final String REMOVED = "machine_removed";
    /** Notes kept per tick; more are dropped (and counted), so a runaway mod can't grow this without bound. */
    static final int MAX = 4096;

    private static final class Note {

        final boolean place;
        final int dim;
        final int x;
        final int y;
        final int z;
        final int mteId; // breaks: the machine there at the break
        final String registry; // breaks: the block there at the break

        Note(boolean place, int dim, int x, int y, int z, int mteId, String registry) {
            this.place = place;
            this.dim = dim;
            this.x = x;
            this.y = y;
            this.z = z;
            this.mteId = mteId;
            this.registry = registry;
        }
    }

    private final List<Note> notes = new ArrayList<>();
    private int dropped;

    void placed(int dim, int x, int y, int z) {
        add(new Note(true, dim, x, y, z, 0, null));
    }

    /** A break of the GT machine {@code mteId} (block {@code registry}) is about to happen. */
    void breaking(int dim, int x, int y, int z, int mteId, String registry) {
        if (mteId > 0) add(new Note(false, dim, x, y, z, mteId, registry));
    }

    private void add(Note n) {
        if (notes.size() >= MAX) {
            dropped++;
            return;
        }
        notes.add(n);
    }

    boolean isEmpty() {
        return notes.isEmpty();
    }

    /** Notes dropped since the last call (the census catches what they would have said). */
    int takeDropped() {
        int d = dropped;
        dropped = 0;
        return d;
    }

    /**
     * The tick's events, in the order they happened, each position at most once per kind per tick. A machine placed
     * and broken within one tick gives only its {@code machine_removed} (the core ignores a removal of a position it
     * never had).
     */
    List<Event> settle(World world) {
        List<Event> out = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (Note n : notes) {
            String key = (n.place ? "p" : "b") + n.dim + ":" + n.x + "," + n.y + "," + n.z;
            if (!seen.add(key)) continue;
            int now = world.mteAt(n.dim, n.x, n.y, n.z);
            if (n.place) {
                if (now > 0) {
                    out.add(new Event(BUILT, n.dim, n.x, n.y, n.z, now, world.blockAt(n.dim, n.x, n.y, n.z)));
                }
            } else if (now != n.mteId) {
                out.add(new Event(REMOVED, n.dim, n.x, n.y, n.z, n.mteId, n.registry));
            }
        }
        notes.clear();
        return out;
    }
}
