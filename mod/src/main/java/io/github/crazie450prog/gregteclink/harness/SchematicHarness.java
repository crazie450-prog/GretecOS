package io.github.crazie450prog.gregteclink.harness;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

import net.minecraft.block.Block;
import net.minecraft.item.ItemStack;
import net.minecraft.nbt.CompressedStreamTools;
import net.minecraft.nbt.NBTTagCompound;
import net.minecraft.nbt.NBTTagList;
import net.minecraft.server.MinecraftServer;
import net.minecraft.tileentity.TileEntity;
import net.minecraft.world.WorldServer;
import net.minecraftforge.common.util.FakePlayer;
import net.minecraftforge.common.util.FakePlayerFactory;
import net.minecraftforge.common.util.ForgeDirection;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;
import com.sk89q.worldedit.EditSession;
import com.sk89q.worldedit.Vector;
import com.sk89q.worldedit.WorldEdit;
import com.sk89q.worldedit.extent.clipboard.BlockArrayClipboard;
import com.sk89q.worldedit.extent.clipboard.Clipboard;
import com.sk89q.worldedit.extent.clipboard.io.ClipboardFormat;
import com.sk89q.worldedit.extent.clipboard.io.ClipboardWriter;
import com.sk89q.worldedit.forge.ForgeWorld;
import com.sk89q.worldedit.forge.ForgeWorldEdit;
import com.sk89q.worldedit.function.operation.ForwardExtentCopy;
import com.sk89q.worldedit.function.operation.Operation;
import com.sk89q.worldedit.function.operation.Operations;
import com.sk89q.worldedit.regions.CuboidRegion;
import com.sk89q.worldedit.session.ClipboardHolder;
import com.sk89q.worldedit.world.registry.WorldData;

import cpw.mods.fml.common.FMLCommonHandler;
import cpw.mods.fml.common.eventhandler.SubscribeEvent;
import cpw.mods.fml.common.gameevent.TickEvent;
import gregtech.api.GregTechAPI;
import gregtech.api.interfaces.metatileentity.IMetaTileEntity;
import gregtech.api.interfaces.tileentity.IGregTechTileEntity;
import gregtech.api.metatileentity.implementations.MTEHatchEnergy;
import gregtech.api.metatileentity.implementations.MTEHatchInputBus;
import gregtech.api.metatileentity.implementations.MTEHatchOutputBus;
import gregtech.common.tileentities.machines.multi.MTEElectricBlastFurnace;
import io.github.crazie450prog.gregteclink.GregtecLink;
import io.github.crazie450prog.gregteclink.export.Json;
import io.github.crazie450prog.gregteclink.export.PathGuard;

/**
 * The schematic harness ({@code ./gradlew runServer -Pharness -PharnessSchematic}, WO-10.4, D310): worldedit-gtnh
 * v0.0.10 in the dev server, no GregtecLink module on. For each GOS-written shell in
 * {@code gregteclink/schematic-harness/in/} (from {@code mod/harness/schematic/in/},
 * {@code tests/harness/schematic_inputs.py}) it does what {@code //schem load}, {@code //paste}, {@code //copy} and
 * {@code //schem save} do, through WorldEdit's own classes: reads the file with {@code ClipboardFormat.SCHEMATIC},
 * pastes it at a "player" position in the superflat world (air included, as a plain {@code //paste}), then copies
 * the box where GOS says the shell lands (the player plus the file's {@code WEOffset}, read here from the NBT, not
 * from WorldEdit) with the player position as the clipboard's origin, and saves it to
 * {@code gregteclink/schematic-harness/out/}. The harness checks the world itself: every cell of that box holds the
 * block (by registry name) and meta WorldEdit read for it, and the cells around the box are untouched air.
 * {@code tests/integration/test_schematic_harness.py} then reads the copies with GOS's reader and compares them with
 * the inputs, cell by cell by registry name + meta. A throwaway world: the harness deletes it before each run.
 */
final class SchematicHarness {

    static final String IN = "gregteclink/schematic-harness/in";
    static final String OUT = "gregteclink/schematic-harness/out";
    /** WorldEdit registers its platform at server start; act a little later. */
    private static final int START_TICK = 40;
    /** Paste spots: well above the superflat ground (y 1-3), one shell every 64 blocks along x. */
    private static final int Y = 100;
    private static final int SPACING = 64;

    private static File gameDir;
    private static int tick;
    private static boolean done;

    private SchematicHarness() {}

    static void start(File dir) {
        gameDir = dir;
        FMLCommonHandler.instance()
            .bus()
            .register(new Ticker());
    }

    public static final class Ticker {

        @SubscribeEvent
        public void onTick(TickEvent.ServerTickEvent e) {
            if (e.phase != TickEvent.Phase.END || done || ++tick < START_TICK) return;
            done = true;
            List<JsonObject> checks = new ArrayList<>();
            JsonObject durations = new JsonObject();
            long t0 = System.currentTimeMillis();
            try {
                run(checks);
            } catch (Throwable t) {
                GregtecLink.LOG.error("schematic harness failed", t);
                Harness.check(checks, "harness_ran", false, Json.error(t));
            }
            durations.addProperty("schematic_ms", System.currentTimeMillis() - t0);
            boolean pass = Harness.writeResult(gameDir, checks, null, durations, null);
            Harness.finished(pass);
            GregtecLink.LOG.info("schematic harness {}: shutting down", pass ? "PASSED" : "FAILED");
            MinecraftServer.getServer()
                .initiateShutdown();
        }
    }

    private static void run(List<JsonObject> checks) throws Exception {
        PathGuard in = new PathGuard(gameDir.toPath(), IN);
        PathGuard out = new PathGuard(gameDir.toPath(), OUT);

        List<Path> files = new ArrayList<>();
        try (DirectoryStream<Path> ds = Files.newDirectoryStream(in.root(), "*.schematic")) {
            for (Path p : ds) files.add(p);
        }
        files.sort(null);
        Harness.check(checks, "inputs_found", files.size() >= 2, files.size() + " .schematic files in " + IN);
        WorldServer mc = MinecraftServer.getServer()
            .worldServerForDimension(0);
        ForgeWorld world = ForgeWorldEdit.inst.getWorld(mc);
        WorldData data = world.getWorldData();
        int k = 0;
        for (Path file : files) {
            String name = file.getFileName()
                .toString();
            Vector player = new Vector(SPACING * k++, Y, 0);
            NBTTagCompound tag;
            try (InputStream s = new FileInputStream(file.toFile())) {
                tag = CompressedStreamTools.readCompressed(s);
            }
            int w = tag.getShort("Width"), h = tag.getShort("Height"), l = tag.getShort("Length");
            Vector offset = new Vector(
                tag.getInteger("WEOffsetX"),
                tag.getInteger("WEOffsetY"),
                tag.getInteger("WEOffsetZ"));
            Vector min = player.add(offset);
            Vector max = min.add(w - 1, h - 1, l - 1);

            // A player has the chunks around it loaded; with no player on the server, unloaded chunks are the empty
            // chunk, which takes and drops every block. Load the box and the cells around it.
            for (int cx = (min.getBlockX() - 1) >> 4; cx <= (max.getBlockX() + 1) >> 4; cx++) {
                for (int cz = (min.getBlockZ() - 1) >> 4; cz <= (max.getBlockZ() + 1) >> 4; cz++) {
                    mc.theChunkProviderServer.loadChunk(cx, cz);
                }
            }

            // //schem load + //paste (air included)
            Clipboard clipboard;
            try (InputStream s = new BufferedInputStream(new FileInputStream(file.toFile()))) {
                clipboard = ClipboardFormat.SCHEMATIC.getReader(s)
                    .read(data);
            }
            EditSession paste = WorldEdit.getInstance()
                .getEditSessionFactory()
                .getEditSession(world, -1);
            Operation op = new ClipboardHolder(clipboard, data).createPaste(paste, data)
                .to(player)
                .ignoreAirBlocks(false)
                .build();
            Operations.completeLegacy(op);
            paste.flushQueue();
            GregtecLink.LOG.info(
                "schematic harness: {} pasted, {} blocks changed; WorldEdit reads {} at {}",
                name,
                paste.getBlockChangeCount(),
                world.getBlock(min),
                min);

            // the world, read back by registry name + meta
            Vector cmin = clipboard.getRegion()
                .getMinimumPoint();
            int wrong = 0, solid = 0;
            String first = "";
            for (int y = 0; y < h; y++) {
                for (int z = 0; z < l; z++) {
                    for (int x = 0; x < w; x++) {
                        com.sk89q.worldedit.blocks.BaseBlock want = clipboard.getBlock(cmin.add(x, y, z));
                        String wantName = Block.blockRegistry.getNameForObject(Block.getBlockById(want.getId()));
                        int gx = min.getBlockX() + x, gy = min.getBlockY() + y, gz = min.getBlockZ() + z;
                        String gotName = Block.blockRegistry.getNameForObject(mc.getBlock(gx, gy, gz));
                        int gotMeta = mc.getBlockMetadata(gx, gy, gz);
                        if (want.getId() != 0) solid++;
                        if (!wantName.equals(gotName) || want.getData() != gotMeta) {
                            if (wrong++ == 0) first = String.format(
                                "(%d, %d, %d): %s@%d in the world, %s@%d in the file",
                                x,
                                y,
                                z,
                                gotName,
                                gotMeta,
                                wantName,
                                want.getData());
                        }
                    }
                }
            }
            Harness.check(
                checks,
                "pasted_at_offset_" + name,
                wrong == 0 && solid > 0,
                solid + " solid cells at player "
                    + player
                    + " + WEOffset "
                    + offset
                    + (wrong == 0 ? "" : "; " + wrong + " cells differ, first " + first));
            int outside = 0;
            for (int x = min.getBlockX() - 1; x <= max.getBlockX() + 1; x++) {
                for (int z = min.getBlockZ() - 1; z <= max.getBlockZ() + 1; z++) {
                    if (!mc.isAirBlock(x, max.getBlockY() + 1, z)) outside++;
                    if (!mc.isAirBlock(x, min.getBlockY() - 1, z)) outside++;
                    boolean ring = x < min.getBlockX() || x > max.getBlockX()
                        || z < min.getBlockZ()
                        || z > max.getBlockZ();
                    if (!ring) continue;
                    for (int y = min.getBlockY(); y <= max.getBlockY(); y++) {
                        if (!mc.isAirBlock(x, y, z)) outside++;
                    }
                }
            }
            Harness.check(
                checks,
                "nothing_outside_" + name,
                outside == 0,
                outside + " non-air cells around the box (above, below and its four sides)");

            // //copy (origin = the player) + //schem save
            CuboidRegion region = new CuboidRegion(world, min, max);
            BlockArrayClipboard copy = new BlockArrayClipboard(region);
            copy.setOrigin(player);
            EditSession source = WorldEdit.getInstance()
                .getEditSessionFactory()
                .getEditSession(world, -1);
            Operations.completeLegacy(new ForwardExtentCopy(source, region, copy, region.getMinimumPoint()));
            Path target = out.resolve(name);
            try (OutputStream s = new BufferedOutputStream(Files.newOutputStream(target));
                ClipboardWriter writer = ClipboardFormat.SCHEMATIC.getWriter(s)) {
                writer.write(copy, data);
            }
            Harness.check(checks, "copied_" + name, Files.size(target) > 0, OUT + "/" + name);
        }
        captureMulti(checks, mc, world, data, out, new Vector(SPACING * k, Y, 0));
    }

    /** The GT multiblock capture's file and what was placed (WO-10.9). */
    static final String CAPTURE = "gt_multi_capture.schematic";
    static final String PLACED = "gt_multi_capture.placed.json";
    /** The casing around the machines: GT's heat proof machine casing. */
    private static final int CASING_META = 11;

    /**
     * WO-10.9 (D350): what Clayton does in the Lab for an addon multi, with a GT multiblock controller the dev runtime
     * has (the EBF stands in for an addon multi: the dev runtime holds GT5-Unofficial only). A 3 x 2 x 3 block of
     * heat proof casings with the controller in its east face (its front turned east, so the capture is not in the
     * extractor's north), an input bus, an output bus and an energy hatch, each machine placed as a player does (the
     * item's use, so GT writes its tile entity) next to a casing; then {@code //copy} with one air column to the west
     * and {@code //schem save} through WorldEdit's own classes, with the tile entities, as in the Lab. What was placed
     * (box coordinates of the copy, MTE ids, the facing) goes to {@link #PLACED} for
     * {@code tests/integration/test_footprints_schematic_harness.py}, which turns the copy into a footprint.
     */
    private static void captureMulti(List<JsonObject> checks, WorldServer mc, ForgeWorld world, WorldData data,
        PathGuard out, Vector base) throws Exception {
        IMetaTileEntity[] all = GregTechAPI.METATILEENTITIES;
        int controller = all[1000] instanceof MTEElectricBlastFurnace ? 1000
            : firstOf(all, MTEElectricBlastFurnace.class);
        int inBus = firstOf(all, MTEHatchInputBus.class);
        int outBus = firstOf(all, MTEHatchOutputBus.class);
        int energy = firstOf(all, MTEHatchEnergy.class);
        // box coordinates of the copy (x 0 is the air column), and the MTE id at each machine cell
        int[][] machines = { { 3, 0, 1, controller }, { 1, 0, 0, inBus }, { 1, 0, 2, outBus }, { 2, 1, 1, energy } };
        Vector min = base;
        Vector max = base.add(3, 1, 2);
        for (int cx = (min.getBlockX() - 1) >> 4; cx <= (max.getBlockX() + 1) >> 4; cx++) {
            for (int cz = (min.getBlockZ() - 1) >> 4; cz <= (max.getBlockZ() + 1) >> 4; cz++) {
                mc.theChunkProviderServer.loadChunk(cx, cz);
            }
        }
        for (int x = 1; x <= 3; x++) {
            for (int y = 0; y <= 1; y++) {
                for (int z = 0; z <= 2; z++) {
                    mc.setBlock(
                        min.getBlockX() + x,
                        min.getBlockY() + y,
                        min.getBlockZ() + z,
                        GregTechAPI.sBlockCasings1,
                        CASING_META,
                        3);
                }
            }
        }
        FakePlayer player = FakePlayerFactory.getMinecraft(mc);
        int placed = 0;
        String wrong = "";
        for (int[] m : machines) {
            int x = min.getBlockX() + m[0], y = min.getBlockY() + m[1], z = min.getBlockZ() + m[2];
            mc.setBlockToAir(x, y, z);
            // click the casing below (y 1) or west of it (y 0, x 1..3: the casing at x - 1 or x + 1), on the face
            // toward the cell, as a player placing it does
            int[] from = m[1] == 1 ? new int[] { x, y - 1, z, 1 }
                : m[0] > 1 ? new int[] { x - 1, y, z, 5 } : new int[] { x + 1, y, z, 4 };
            ItemStack stack = new ItemStack(GregTechAPI.sBlockMachines, 1, m[3]);
            player.setCurrentItemOrArmor(0, stack);
            stack.tryPlaceItemIntoWorld(player, mc, from[0], from[1], from[2], from[3], 0.5f, 0.5f, 0.5f);
            TileEntity te = mc.getTileEntity(x, y, z);
            if (te instanceof IGregTechTileEntity && ((IGregTechTileEntity) te).getMetaTileID() == m[3]) {
                placed++;
            } else if (wrong.isEmpty()) {
                wrong = "; MTE " + m[3] + " not placed at box (" + m[0] + ", " + m[1] + ", " + m[2] + ")";
            }
        }
        TileEntity ct = mc.getTileEntity(min.getBlockX() + 3, min.getBlockY(), min.getBlockZ() + 1);
        boolean east = false;
        if (ct instanceof IGregTechTileEntity) {
            ((IGregTechTileEntity) ct).setFrontFacing(ForgeDirection.EAST);
            east = ((IGregTechTileEntity) ct).getFrontFacing() == ForgeDirection.EAST;
        }
        Harness.check(
            checks,
            "multi_placed",
            placed == machines.length && east,
            placed + " of "
                + machines.length
                + " GT machines placed (controller MTE "
                + controller
                + " facing "
                + (east ? "east" : "not east")
                + ", buses "
                + inBus
                + "/"
                + outBus
                + ", energy "
                + energy
                + ")"
                + wrong);

        // //copy (one air column west of the casings, origin a "player" south-west of it) + //schem save
        CuboidRegion region = new CuboidRegion(world, min, max);
        BlockArrayClipboard copy = new BlockArrayClipboard(region);
        copy.setOrigin(base.add(1, 0, -2));
        EditSession source = WorldEdit.getInstance()
            .getEditSessionFactory()
            .getEditSession(world, -1);
        Operations.completeLegacy(new ForwardExtentCopy(source, region, copy, region.getMinimumPoint()));
        Path target = out.resolve(CAPTURE);
        try (OutputStream s = new BufferedOutputStream(Files.newOutputStream(target));
            ClipboardWriter writer = ClipboardFormat.SCHEMATIC.getWriter(s)) {
            writer.write(copy, data);
        }
        // the save kept GT's tile entities with their MTE ids (what WO-10.9's reader needs)
        NBTTagCompound saved;
        try (InputStream s = new FileInputStream(target.toFile())) {
            saved = CompressedStreamTools.readCompressed(s);
        }
        NBTTagList tiles = saved.getTagList("TileEntities", 10);
        int withId = 0;
        for (int i = 0; i < tiles.tagCount(); i++) {
            NBTTagCompound t = tiles.getCompoundTagAt(i);
            if ("BaseMetaTileEntity".equals(t.getString("id")) && t.getInteger("mID") > 0) withId++;
        }
        Harness.check(
            checks,
            "multi_copied_with_tile_entities",
            withId == machines.length,
            OUT + "/"
                + CAPTURE
                + ": "
                + tiles.tagCount()
                + " tile entities, "
                + withId
                + " BaseMetaTileEntity with mID");

        JsonObject doc = new JsonObject();
        doc.addProperty("file", CAPTURE);
        doc.addProperty("size", "4x2x3");
        doc.addProperty("casing", Block.blockRegistry.getNameForObject(GregTechAPI.sBlockCasings1) + "@" + CASING_META);
        doc.addProperty("facing", "east");
        JsonArray list = new JsonArray();
        for (int[] m : machines) {
            JsonObject o = new JsonObject();
            JsonArray p = new JsonArray();
            for (int i = 0; i < 3; i++) p.add(new JsonPrimitive(m[i]));
            o.add("p", p);
            o.addProperty("mte", m[3]);
            o.addProperty("controller", m[3] == controller && m[0] == 3);
            list.add(o);
        }
        doc.add("machines", list);
        Files.write(
            out.resolve(PLACED),
            doc.toString()
                .getBytes(StandardCharsets.UTF_8));
    }

    /** The lowest MTE id of exactly this class (not a subclass: an ME bus is not a plain bus). */
    private static int firstOf(IMetaTileEntity[] all, Class<?> type) {
        for (int id = 1; id < all.length; id++) {
            if (all[id] != null && all[id].getClass() == type) return id;
        }
        throw new IllegalStateException("no " + type.getSimpleName() + " registered");
    }
}
