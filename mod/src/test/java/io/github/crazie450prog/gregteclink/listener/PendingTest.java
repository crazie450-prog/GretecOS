package io.github.crazie450prog.gregteclink.listener;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;

/** The tick-end settle: only changes the world confirms are reported. */
class PendingTest {

    private static final String GT = "gregtech:gt.blockmachines";

    /** A world of GT machines by position. */
    private static final class FakeWorld implements Pending.World {

        final Map<String, Integer> mte = new HashMap<>();

        @Override
        public int mteAt(int dim, int x, int y, int z) {
            Integer id = mte.get(dim + ":" + x + "," + y + "," + z);
            return id == null ? 0 : id;
        }

        @Override
        public String blockAt(int dim, int x, int y, int z) {
            return mteAt(dim, x, y, z) > 0 ? GT : "minecraft:air";
        }

        void put(int x, int y, int z, int id) {
            mte.put("0:" + x + "," + y + "," + z, id);
        }

        void remove(int x, int y, int z) {
            mte.remove("0:" + x + "," + y + "," + z);
        }
    }

    private static List<String> text(List<Pending.Event> events) {
        List<String> out = new ArrayList<>();
        for (Pending.Event e : events) out.add(e.toString());
        return out;
    }

    @Test
    void placementIsReportedWithTheMachineThatStandsThere() {
        FakeWorld w = new FakeWorld();
        Pending p = new Pending();
        p.placed(0, 1, 2, 3);
        w.put(1, 2, 3, 1000);
        List<Pending.Event> got = p.settle(w);
        assertEquals(1, got.size());
        Pending.Event e = got.get(0);
        assertEquals(Pending.BUILT, e.kind);
        assertEquals(1000, e.mteId);
        assertEquals(GT, e.registry);
        assertTrue(p.isEmpty());
    }

    @Test
    void cancelledPlacementOrBreakIsNotReported() {
        FakeWorld w = new FakeWorld();
        Pending p = new Pending();
        p.placed(0, 1, 2, 3); // cancelled later: nothing there at the tick's end
        w.put(5, 2, 3, 66);
        p.breaking(0, 5, 2, 3, 66, GT); // cancelled: the machine is still there
        assertEquals(new ArrayList<String>(), text(p.settle(w)));
    }

    @Test
    void breakIsReportedWithTheMachineSeenAtTheBreak() {
        FakeWorld w = new FakeWorld();
        Pending p = new Pending();
        w.put(5, 2, 3, 66);
        p.breaking(0, 5, 2, 3, 66, GT);
        w.remove(5, 2, 3);
        List<Pending.Event> got = p.settle(w);
        assertEquals(1, got.size());
        assertEquals(Pending.REMOVED, got.get(0).kind);
        assertEquals(66, got.get(0).mteId);
        assertEquals(GT, got.get(0).registry);
    }

    @Test
    void breakingNothingIsNoNote() {
        Pending p = new Pending();
        p.breaking(0, 5, 2, 3, 0, GT);
        assertTrue(p.isEmpty());
    }

    @Test
    void orderIsKeptAndPositionsReportedOncePerKind() {
        FakeWorld w = new FakeWorld();
        Pending p = new Pending();
        w.put(1, 1, 1, 10);
        p.breaking(0, 1, 1, 1, 10, GT);
        p.breaking(0, 1, 1, 1, 10, GT);
        w.remove(1, 1, 1);
        p.placed(0, 2, 1, 1);
        p.placed(0, 2, 1, 1);
        w.put(2, 1, 1, 20);
        assertEquals(
            Arrays.asList("machine_removed mte:10 " + GT + " @0:1,1,1", "machine_built mte:20 " + GT + " @0:2,1,1"),
            text(p.settle(w)));
    }

    @Test
    void notesAreBounded() {
        Pending p = new Pending();
        for (int i = 0; i < Pending.MAX + 5; i++) p.placed(0, i, 1, 1);
        assertEquals(5, p.takeDropped());
        assertEquals(0, p.takeDropped());
        assertEquals(
            0,
            p.settle(new FakeWorld())
                .size());
    }
}
