package io.github.crazie450prog.gregteclink.harness;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

import net.minecraft.block.Block;
import net.minecraft.nbt.CompressedStreamTools;
import net.minecraft.nbt.NBTTagCompound;
import net.minecraft.server.MinecraftServer;
import net.minecraft.world.WorldServer;

import com.google.gson.JsonObject;
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
            Vector offset = new Vector(tag.getInteger("WEOffsetX"), tag.getInteger("WEOffsetY"),
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
                solid + " solid cells at player " + player + " + WEOffset " + offset
                    + (wrong == 0 ? "" : "; " + wrong + " cells differ, first " + first));
            int outside = 0;
            for (int x = min.getBlockX() - 1; x <= max.getBlockX() + 1; x++) {
                for (int z = min.getBlockZ() - 1; z <= max.getBlockZ() + 1; z++) {
                    if (!mc.isAirBlock(x, max.getBlockY() + 1, z)) outside++;
                    if (!mc.isAirBlock(x, min.getBlockY() - 1, z)) outside++;
                }
            }
            Harness.check(
                checks,
                "nothing_outside_" + name,
                outside == 0,
                outside + " non-air cells in the layers above and below the box");

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
    }
}
