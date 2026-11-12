package io.github.crazie450prog.gregteclink.harness;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import net.minecraft.item.ItemStack;
import net.minecraft.server.MinecraftServer;
import net.minecraft.world.WorldServer;
import net.minecraft.world.storage.ThreadedFileIOBase;
import net.minecraftforge.common.DimensionManager;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.common.util.FakePlayer;
import net.minecraftforge.common.util.FakePlayerFactory;
import net.minecraftforge.event.world.BlockEvent;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import cpw.mods.fml.common.FMLCommonHandler;
import cpw.mods.fml.common.eventhandler.EventPriority;
import cpw.mods.fml.common.eventhandler.SubscribeEvent;
import cpw.mods.fml.common.gameevent.TickEvent;
import cpw.mods.fml.common.registry.GameData;
import gregtech.api.GregTechAPI;
import gregtech.api.interfaces.metatileentity.IMetaTileEntity;
import gregtech.api.metatileentity.implementations.MTEBasicMachine;
import gregtech.api.metatileentity.implementations.MTEHatch;
import gregtech.api.metatileentity.implementations.MTEMultiBlockBase;
import io.github.crazie450prog.gregteclink.GregtecLink;
import io.github.crazie450prog.gregteclink.ModulesConfig;
import io.github.crazie450prog.gregteclink.export.Json;
import io.github.crazie450prog.gregteclink.export.PathGuard;
import io.github.crazie450prog.gregteclink.listener.EventFile;
import io.github.crazie450prog.gregteclink.listener.ListenerModule;
import io.github.crazie450prog.gregteclink.listener.MachineListener;

/**
 * The listener's server harness ({@code ./gradlew runServer -Pharness -PharnessListener}, WO-6.2, D261), with the
 * listener module on and export off. A fake player places 10 GT machine kinds (a multiblock controller, a hatch and 8
 * single-block machines) on the superflat world through the item's real path ({@code tryPlaceItemIntoWorld}, which
 * fires Forge's {@code PlaceEvent}) and breaks four of them through {@code tryHarvestBlock} ({@code BreakEvent}); one
 * machine is placed and one removed without a player event (the listener can't see those: the census must), and one
 * placement and one break are cancelled by another handler (no event may come of them). After each phase the world is
 * saved and its region files are copied to {@code gregteclink/listener-harness/census-a|b/}; {@code expected.json}
 * there says which machines stand where after each phase, for the Python census and My base tests
 * ({@code tests/integration/test_machine_events_harness.py}). The harness checks the events file itself.
 */
final class ListenerHarness {

    static final String PROPERTY = "gregtec.harness.listener";
    static final String OUT = "gregteclink/listener-harness";
    private static final int KINDS = 10;
    /** Preferred kinds (GT 5.09.54.265 ids); any that are missing are filled from the registry by type. */
    private static final int EBF = 1000;
    private static final int INPUT_BUS_LV = 66;
    private static final Set<String> FORBIDDEN_KEYS = new HashSet<>(
        Arrays.asList("id", "item_id", "block_id", "blockId", "itemId", "idExt"));

    private static File gameDir;
    private static ModulesConfig modules;
    private static Map<String, String> before;
    private static int tick;
    private static final List<JsonObject> checks = new ArrayList<>();
    private static final List<Machine> placed = new ArrayList<>();
    /** pos key -> mte id, the base after each phase. */
    private static final Map<String, Integer> baseA = new LinkedHashMap<>();
    private static final Map<String, Integer> baseB = new LinkedHashMap<>();
    private static final List<String> expectedLines = new ArrayList<>();
    private static Machine silentPlaced;
    private static Machine silentRemoved;
    private static Machine cancelledBreak;
    private static int[] cancelPlaceAt;
    private static int[] cancelBreakAt;
    private static boolean done;

    private ListenerHarness() {}

    static boolean requested() {
        return Boolean.getBoolean(PROPERTY);
    }

    private static final class Machine {

        final int mteId;
        final int x;
        final int y;
        final int z;
        final String kind;

        Machine(int mteId, int x, int y, int z, String kind) {
            this.mteId = mteId;
            this.x = x;
            this.y = y;
            this.z = z;
            this.kind = kind;
        }

        String key() {
            return "0:" + x + "," + y + "," + z;
        }
    }

    static void start(File dir, ModulesConfig m) {
        gameDir = dir;
        modules = m;
        try {
            before = Harness.snapshot(
                gameDir,
                MinecraftServer.getServer()
                    .getFolderName());
        } catch (IOException e) {
            fail("snapshot", e);
        }
        FMLCommonHandler.instance()
            .bus()
            .register(new Ticker());
        MinecraftForge.EVENT_BUS.register(new Canceller());
    }

    /** Another mod cancelling one placement and one break, at the highest priority. */
    public static final class Canceller {

        @SubscribeEvent(priority = EventPriority.HIGHEST)
        public void onPlace(BlockEvent.PlaceEvent e) {
            if (at(cancelPlaceAt, e.x, e.y, e.z)) e.setCanceled(true);
        }

        @SubscribeEvent(priority = EventPriority.HIGHEST)
        public void onBreak(BlockEvent.BreakEvent e) {
            if (at(cancelBreakAt, e.x, e.y, e.z)) e.setCanceled(true);
        }

        private static boolean at(int[] p, int x, int y, int z) {
            return p != null && p[0] == x && p[1] == y && p[2] == z;
        }
    }

    public static final class Ticker {

        @SubscribeEvent
        public void onTick(TickEvent.ServerTickEvent e) {
            if (e.phase != TickEvent.Phase.END || done) return;
            tick++;
            try {
                if (tick == 2) phaseA();
                else if (tick == 6) saveAndCopy("census-a", baseA);
                else if (tick == 8) phaseB();
                else if (tick == 12) {
                    saveAndCopy("census-b", baseB);
                    finish();
                }
            } catch (Throwable t) {
                fail("step at tick " + tick, t);
                finish();
            }
        }
    }

    private static void fail(String what, Throwable t) {
        GregtecLink.LOG.error("listener harness: " + what, t);
        Harness.check(checks, "listener_harness_ran", false, what + ": " + Json.error(t));
    }

    private static WorldServer world() {
        return DimensionManager.getWorld(0);
    }

    /** The 10 kinds: EBF (a multiblock controller), an LV input bus (a hatch), 8 single-block machine classes. */
    private static List<Integer> kinds() {
        IMetaTileEntity[] all = GregTechAPI.METATILEENTITIES;
        List<Integer> out = new ArrayList<>();
        if (all[EBF] instanceof MTEMultiBlockBase) out.add(EBF);
        else out.add(firstOf(all, MTEMultiBlockBase.class, out));
        if (all[INPUT_BUS_LV] instanceof MTEHatch) out.add(INPUT_BUS_LV);
        else out.add(firstOf(all, MTEHatch.class, out));
        Set<Class<?>> classes = new HashSet<>();
        for (int id = 1; id < all.length && out.size() < KINDS; id++) {
            if (all[id] instanceof MTEBasicMachine && classes.add(all[id].getClass())) out.add(id);
        }
        return out;
    }

    private static int firstOf(IMetaTileEntity[] all, Class<?> type, List<Integer> taken) {
        for (int id = 1; id < all.length; id++) {
            if (type.isInstance(all[id]) && !taken.contains(id)) return id;
        }
        throw new IllegalStateException("no " + type.getSimpleName() + " registered");
    }

    private static String kindOf(int id) {
        IMetaTileEntity m = GregTechAPI.METATILEENTITIES[id];
        if (m instanceof MTEMultiBlockBase) return "multiblock_controller";
        if (m instanceof MTEHatch) return "hatch";
        return "machine";
    }

    private static FakePlayer player(WorldServer w) {
        return FakePlayerFactory.getMinecraft(w);
    }

    /** Places {@code id} on the bedrock at (x, 0, z) as a player does: the item's use, with Forge's PlaceEvent. */
    private static boolean playerPlace(WorldServer w, int id, int x, int z) {
        FakePlayer p = player(w);
        ItemStack stack = new ItemStack(GregTechAPI.sBlockMachines, 1, id);
        p.setCurrentItemOrArmor(0, stack);
        return stack.tryPlaceItemIntoWorld(p, w, x, 0, z, 1, 0.5f, 1.0f, 0.5f);
    }

    private static void phaseA() throws IOException {
        WorldServer w = world();
        int bx = w.getSpawnPoint().posX + 3;
        int bz = w.getSpawnPoint().posZ + 3;
        for (int dx = -1; dx <= 2; dx++) {
            for (int dz = -1; dz <= 2; dz++) w.getChunkProvider()
                .loadChunk((bx >> 4) + dx, (bz >> 4) + dz);
        }
        Harness.check(checks, "listener_module_active", ListenerModule.active(), "config: listener=true, export=false");
        Harness.check(checks, "export_module_off", !modules.export, "the listener runs only without export");
        List<Integer> kinds = kinds();
        Harness.check(checks, "ten_kinds", kinds.size() == KINDS, "kinds " + kinds);
        boolean multi = false, hatch = false;
        for (int i = 0; i < kinds.size(); i++) {
            int id = kinds.get(i);
            Machine m = new Machine(id, bx + 3 * i, 1, bz, kindOf(id));
            multi |= m.kind.equals("multiblock_controller");
            hatch |= m.kind.equals("hatch");
            boolean ok = playerPlace(w, id, m.x, m.z);
            if (!ok || MachineListener.mteAt(w, m.x, m.y, m.z) != id) {
                Harness.check(checks, "placed_mte_" + id, false, "the fake player's placement failed at " + m.key());
                continue;
            }
            placed.add(m);
            baseA.put(m.key(), id);
            expectedLines.add(line("machine_built", m));
        }
        Harness.check(
            checks,
            "kinds_include_controller_and_hatch",
            multi && hatch,
            "multiblock " + multi + ", hatch " + hatch);
        // A placement another mod cancels: no block, no event.
        cancelPlaceAt = new int[] { bx, 1, bz + 3 };
        boolean cancelled = playerPlace(w, kinds.get(2), bx, bz + 3);
        Harness.check(
            checks,
            "cancelled_place_left_nothing",
            !cancelled && MachineListener.mteAt(w, bx, 1, bz + 3) == 0,
            "PlaceEvent cancelled at HIGHEST");
        // A machine placed without a player event (as some mods' placers do): the listener can't see it.
        silentPlaced = new Machine(kinds.get(2), bx, 1, bz + 6, "machine");
        ItemStack stack = new ItemStack(GregTechAPI.sBlockMachines, 1, silentPlaced.mteId);
        boolean silentOk = stack.getItem()
            .onItemUse(stack, player(w), w, silentPlaced.x, 0, silentPlaced.z, 1, 0.5f, 1.0f, 0.5f)
            && MachineListener.mteAt(w, silentPlaced.x, 1, silentPlaced.z) == silentPlaced.mteId;
        Harness.check(checks, "silent_place", silentOk, "placed with no PlaceEvent at " + silentPlaced.key());
        if (silentOk) baseA.put(silentPlaced.key(), silentPlaced.mteId);
        // Reading never loads a chunk: a far, unloaded position stays unloaded.
        int far = 100_000;
        int got = MachineListener.mteAt(w, far, 1, far);
        Harness.check(
            checks,
            "read_never_loads_chunks",
            got == 0 && !w.getChunkProvider()
                .chunkExists(far >> 4, far >> 4),
            "mteAt on an unloaded chunk");
    }

    private static void phaseB() {
        WorldServer w = world();
        FakePlayer p = player(w);
        p.setCurrentItemOrArmor(0, null);
        baseB.putAll(baseA);
        // Break the controller, the hatch and two machines as a player does.
        List<Machine> breaks = new ArrayList<>(placed.subList(0, Math.min(4, placed.size())));
        for (Machine m : breaks) {
            boolean ok = p.theItemInWorldManager.tryHarvestBlock(m.x, m.y, m.z);
            boolean gone = MachineListener.mteAt(w, m.x, m.y, m.z) == 0;
            Harness.check(checks, "broke_" + m.kind + "_" + m.mteId, ok && gone, "tryHarvestBlock at " + m.key());
            if (gone) {
                baseB.remove(m.key());
                expectedLines.add(line("machine_removed", m));
            }
        }
        // A break another mod cancels: the machine stays, no event.
        if (placed.size() > 5) {
            cancelledBreak = placed.get(5);
            cancelBreakAt = new int[] { cancelledBreak.x, cancelledBreak.y, cancelledBreak.z };
            // Posted as ForgeHooks.onBlockBreakEvent posts it: its cancelled path sends the player a block-update
            // packet, which a fake player has no connection for. Cancelled, the block stays, as in tryHarvestBlock.
            int x = cancelledBreak.x, y = cancelledBreak.y, z = cancelledBreak.z;
            BlockEvent.BreakEvent ev = new BlockEvent.BreakEvent(
                x,
                y,
                z,
                w,
                w.getBlock(x, y, z),
                w.getBlockMetadata(x, y, z),
                p);
            MinecraftForge.EVENT_BUS.post(ev);
            Harness.check(
                checks,
                "cancelled_break_kept_machine",
                ev.isCanceled() && MachineListener.mteAt(w, x, y, z) == cancelledBreak.mteId,
                "BreakEvent cancelled at HIGHEST");
        }
        // A machine removed without a player event (an explosion, another mod): only the census sees it.
        if (placed.size() > 6) {
            silentRemoved = placed.get(6);
            w.setBlockToAir(silentRemoved.x, silentRemoved.y, silentRemoved.z);
            boolean gone = MachineListener.mteAt(w, silentRemoved.x, silentRemoved.y, silentRemoved.z) == 0;
            Harness.check(checks, "silent_remove", gone, "setBlockToAir at " + silentRemoved.key());
            if (gone) baseB.remove(silentRemoved.key());
        }
    }

    private static String line(String kind, Machine m) {
        return kind + " " + m.mteId + " " + m.key();
    }

    /** Saves the world (and waits for the chunk writer), then copies its region files under {@link #OUT}. */
    private static void saveAndCopy(String name, Map<String, Integer> base) throws Exception {
        WorldServer w = world();
        w.saveAllChunks(true, null);
        ThreadedFileIOBase.threadedIOInstance.waitForFinish();
        Path region = w.getSaveHandler()
            .getWorldDirectory()
            .toPath()
            .resolve("region");
        PathGuard guard = new PathGuard(gameDir.toPath(), OUT + "/" + name + "/region");
        int copied = 0;
        try (DirectoryStream<Path> s = Files.newDirectoryStream(region, "*.mca")) {
            for (Path f : s) {
                Files.copy(
                    f,
                    guard.resolve(
                        f.getFileName()
                            .toString()),
                    StandardCopyOption.REPLACE_EXISTING);
                copied++;
            }
        }
        Harness
            .check(checks, "saved_" + name, copied > 0, copied + " region files copied, " + base.size() + " machines");
    }

    private static void finish() {
        done = true;
        try {
            checkEvents();
            writeExpected();
            Map<String, String> after = Harness.snapshot(
                gameDir,
                MinecraftServer.getServer()
                    .getFolderName());
            Harness.checkWrites(checks, "writes_only_under_gregteclink", before, after, Arrays.asList("gregteclink/"));
        } catch (Throwable t) {
            fail("finish", t);
        }
        JsonObject counts = new JsonObject();
        counts.addProperty("events_written", ListenerModule.written());
        counts.addProperty("machines_a", baseA.size());
        counts.addProperty("machines_b", baseB.size());
        boolean pass = Harness.writeResult(gameDir, checks, "listener", new JsonObject(), counts);
        Harness.finished(pass);
        GregtecLink.LOG.info("listener harness {}: shutting down", pass ? "PASSED" : "FAILED");
        MinecraftServer.getServer()
            .initiateShutdown();
    }

    /** The events file says exactly what the player did, in order: built x10, removed x4, nothing else. */
    private static void checkEvents() throws IOException {
        Path path = ListenerModule.eventsPath();
        List<String> lines = path == null || !Files.isRegularFile(path) ? new ArrayList<>()
            : Files.readAllLines(path, StandardCharsets.UTF_8);
        String registry = GameData.getBlockRegistry()
            .getNameForObject(GregTechAPI.sBlockMachines)
            .toString();
        List<String> got = new ArrayList<>();
        List<String> problems = new ArrayList<>();
        long boot = -1;
        long seq = 0;
        for (String raw : lines) {
            JsonObject o = new JsonParser().parse(raw)
                .getAsJsonObject();
            for (Map.Entry<String, JsonElement> e : o.entrySet()) {
                if (FORBIDDEN_KEYS.contains(e.getKey())) problems.add("numeric id key " + e.getKey());
            }
            if (o.get("v")
                .getAsInt() != EventFile.VERSION) problems.add("v");
            if (!registry.equals(
                o.get("registry")
                    .getAsString()))
                problems.add("registry " + o.get("registry"));
            if (o.get("meta")
                .getAsInt()
                != o.get("mte_id")
                    .getAsInt())
                problems.add("meta != mte_id");
            long b = o.get("boot")
                .getAsLong();
            if (boot >= 0 && b != boot) problems.add("boot changed");
            boot = b;
            long s = o.get("seq")
                .getAsLong();
            if (s != seq + 1) problems.add("seq " + s + " after " + seq);
            seq = s;
            got.add(
                o.get("kind")
                    .getAsString() + " "
                    + o.get("mte_id")
                        .getAsInt()
                    + " "
                    + o.get("dim")
                        .getAsInt()
                    + ":"
                    + o.get("x")
                        .getAsInt()
                    + ","
                    + o.get("y")
                        .getAsInt()
                    + ","
                    + o.get("z")
                        .getAsInt());
        }
        Harness.check(
            checks,
            "events_match_actions",
            got.equals(expectedLines) && expectedLines.size() == KINDS + 4,
            got.equals(expectedLines) ? got.size() + " events, registry " + registry
                : "expected " + expectedLines + " got " + got);
        Harness.check(
            checks,
            "event_lines_valid",
            problems.isEmpty(),
            problems.isEmpty() ? "v1, registry + meta, seq" : problems.toString());
    }

    private static void writeExpected() throws IOException {
        JsonObject o = new JsonObject();
        o.addProperty(
            "registry",
            GameData.getBlockRegistry()
                .getNameForObject(GregTechAPI.sBlockMachines)
                .toString());
        JsonArray kinds = new JsonArray();
        for (Machine m : placed) {
            JsonObject k = new JsonObject();
            k.addProperty("mte_id", m.mteId);
            k.addProperty("kind", m.kind);
            k.addProperty(
                "class",
                GregTechAPI.METATILEENTITIES[m.mteId].getClass()
                    .getName());
            kinds.add(k);
        }
        o.add("kinds", kinds);
        o.add("census_a", base(baseA));
        o.add("census_b", base(baseB));
        o.add("silent_placed", Json.str(silentPlaced == null ? null : silentPlaced.key()));
        o.add("silent_removed", Json.str(silentRemoved == null ? null : silentRemoved.key()));
        o.add("events", Json.str(EventFile.DIR + "/" + EventFile.NAME));
        PathGuard guard = new PathGuard(gameDir.toPath(), OUT);
        Files.write(guard.resolve("expected.json"), (Json.PRETTY.toJson(o) + "\n").getBytes(StandardCharsets.UTF_8));
    }

    private static JsonArray base(Map<String, Integer> base) {
        JsonArray a = new JsonArray();
        for (Map.Entry<String, Integer> e : base.entrySet()) {
            String[] dimPos = e.getKey()
                .split(":");
            String[] xyz = dimPos[1].split(",");
            JsonObject m = new JsonObject();
            m.addProperty("dim", Integer.parseInt(dimPos[0]));
            m.addProperty("x", Integer.parseInt(xyz[0]));
            m.addProperty("y", Integer.parseInt(xyz[1]));
            m.addProperty("z", Integer.parseInt(xyz[2]));
            m.addProperty("mte_id", e.getValue());
            a.add(m);
        }
        return a;
    }
}
