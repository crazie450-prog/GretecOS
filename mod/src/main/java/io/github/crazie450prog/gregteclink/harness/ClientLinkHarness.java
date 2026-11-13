package io.github.crazie450prog.gregteclink.harness;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import java.util.Queue;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.function.BooleanSupplier;

import net.minecraft.block.Block;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.ScaledResolution;
import net.minecraft.client.gui.inventory.GuiChest;
import net.minecraft.client.gui.inventory.GuiContainer;
import net.minecraft.entity.player.EntityPlayerMP;
import net.minecraft.inventory.InventoryBasic;
import net.minecraft.inventory.Slot;
import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraft.nbt.NBTTagCompound;
import net.minecraft.server.MinecraftServer;
import net.minecraft.util.ChatComponentTranslation;
import net.minecraft.util.Vec3;
import net.minecraft.world.World;

import org.lwjgl.input.Mouse;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import cpw.mods.fml.common.FMLCommonHandler;
import cpw.mods.fml.common.eventhandler.SubscribeEvent;
import cpw.mods.fml.common.gameevent.TickEvent;
import cpw.mods.fml.common.registry.GameData;
import io.github.crazie450prog.gregteclink.GregtecLink;
import io.github.crazie450prog.gregteclink.client.ClientModule;
import io.github.crazie450prog.gregteclink.client.Feeds;
import io.github.crazie450prog.gregteclink.client.Targets;
import io.github.crazie450prog.gregteclink.export.Json;

/**
 * The client link's part of the client harness (WO-8.1, D287): {@code ./gradlew runClient25 -Pharness
 * -PharnessClientLink}, with a real core on 127.0.0.1:18765 that {@code tests/harness/client_link_core.py} runs on a
 * DB built from the previous harness export. After the export has finished and been checked, still in the
 * autoexport world: waits for the link; places four blocks (a GT machine, a GT casing, a GT addon's casing, a non-GT
 * mod's block) and looks at each; asks the core to restart and waits for the link to come back; opens a chest GUI
 * with six items (GT, two GT addons, a non-GT mod, vanilla, an IC2 battery with NBT) and hovers over each; for each of
 * the 10 it presses What's this? (the key's own code path) and checks that the core's answer names what the game
 * shows and keys it by registry name and meta (NBT hash for the battery). Then {@code !gos} in chat from the player
 * (answered) and from another name (ignored), and the 1 Hz status count. The checks join the client harness's.
 */
public final class ClientLinkHarness {

    public static final String PROPERTY = "gregtec.harness.clientLink";
    static final String LINK_DIR = "gregteclink-harness";
    static final String RESTART = "restart-core";
    static final long CONNECT_MS = 180_000;
    static final long ANSWER_MS = 20_000;
    static final long RECONNECT_MS = 120_000;
    static final long GOS_MS = 20_000;
    static final long IGNORED_MS = 4_000;

    /** Looked at: placed in the world (registry, meta; the GT machine through its item, so it gets its tile entity). */
    static final String[][] BLOCKS = { { "gregtech:gt.blockmachines", "1000", "item" },
        { "gregtech:gt.blockcasings2", "0", "block" }, { "GoodGenerator:MAR_Casing", "0", "block" },
        { "appliedenergistics2:tile.BlockSkyStone", "1", "block" } };
    /** Hovered in a chest GUI: registry, meta, NBT (one IC2 battery whose charged NBT the harness DB has). */
    static final String[][] ITEMS = { { "gregtech:gt.metaitem.01", "11305", null },
        { "bartworks:gt.bwMetaGeneratedingot", "3", null }, { "miscutils:itemHotIngotArcanite", "0", null },
        { "appliedenergistics2:item.ItemMultiMaterial", "24", null }, { "minecraft:iron_ingot", "0", null },
        { "IC2:itemBatRE", "1", "10000.0" } };
    private static final int[][] OFFSETS = { { 2, 0 }, { -2, 0 }, { 0, 2 }, { 0, -2 } };

    private final File gameDir;
    private final Runnable done;
    private final List<Step> steps = new ArrayList<>();
    private final Queue<Runnable> serverTasks = new ConcurrentLinkedQueue<>();
    private int index;
    private long stepStart;
    private long statusT0;
    private int connectsBefore;
    private int answered;
    private int[][] placed;
    private int gosNotices;
    private GuiChest chest;
    private int[] cursor;
    private boolean flipped;
    private boolean finished;

    /** One step: {@code start} once, then {@code until} each tick until true or {@code timeoutMs}. */
    private static final class Step {

        final String name;
        final Runnable start;
        final BooleanSupplier until;
        final long timeoutMs;
        final Runnable onTimeout;

        Step(String name, Runnable start, BooleanSupplier until, long timeoutMs, Runnable onTimeout) {
            this.name = name;
            this.start = start;
            this.until = until;
            this.timeoutMs = timeoutMs;
            this.onTimeout = onTimeout;
        }
    }

    private ClientLinkHarness(File gameDir, Runnable done) {
        this.gameDir = gameDir;
        this.done = done;
    }

    public static boolean requested() {
        return Boolean.getBoolean(PROPERTY) && Harness.requested();
    }

    /** Runs the script over the next ticks, then {@code done} on the client thread. */
    public static void start(File gameDir, Runnable done) {
        ClientLinkHarness h = new ClientLinkHarness(gameDir, done);
        h.plan();
        FMLCommonHandler.instance()
            .bus()
            .register(h);
        GregtecLink.LOG.info("client link harness: {} steps", h.steps.size());
    }

    private static void check(String name, boolean ok, String detail) {
        Harness.check(Harness.clientChecks(), name, ok, detail);
    }

    private void plan() {
        ClientModule m = ClientModule.instance();
        steps.add(new Step("connect", () -> {}, () -> m != null && m.link.connected(), CONNECT_MS, () -> {}));
        steps.add(new Step("connected", () -> {
            check(
                "client_link_connected",
                m != null && m.link.connected(),
                m == null ? "the client module is off" : "connections: " + m.link.connects());
            if (m != null) connectsBefore = m.link.connects();
        }, () -> true, 1000, () -> {}));
        if (m == null) return;
        steps.add(new Step("place", this::place, this::placedSeen, 20_000, () -> check("blocks_placed", false, "")));
        for (int i = 0; i < BLOCKS.length; i++) {
            final int n = i;
            steps.add(new Step("aim " + n, () -> aim(n), () -> true, 1000, () -> {}));
            ask(m, "look_" + BLOCKS[n][0] + "@" + BLOCKS[n][1], BLOCKS[n][0], Integer.parseInt(BLOCKS[n][1]), false);
        }
        steps.add(new Step("restart", this::requestRestart, () -> !m.link.connected(), 30_000, () -> {}));
        steps.add(
            new Step(
                "reconnect",
                () -> {},
                () -> m.link.connected() && m.link.connects() > connectsBefore,
                RECONNECT_MS,
                () -> {}));
        steps.add(new Step("reconnected", () -> {
            boolean back = m.link.connected() && m.link.connects() > connectsBefore;
            check("reconnected_after_core_restart", back, "connections " + connectsBefore + " -> " + m.link.connects());
        }, () -> true, 1000, () -> {}));
        steps.add(new Step("chest", this::openChest, () -> chest != null && guiReady(), 10_000, () -> {}));
        for (int i = 0; i < ITEMS.length; i++) {
            final int n = i;
            steps.add(
                new Step(
                    "hover " + n,
                    () -> hover(n),
                    this::mouseThere,
                    3000,
                    () -> check("hover_" + n + "_mouse_moved", false, mouse())));
            ask(
                m,
                "hover_" + ITEMS[n][0] + "@" + ITEMS[n][1],
                ITEMS[n][0],
                Integer.parseInt(ITEMS[n][1]),
                ITEMS[n][2] != null);
        }
        steps.add(
            new Step(
                "close",
                () -> Minecraft.getMinecraft()
                    .displayGuiScreen(null),
                () -> true,
                1000,
                () -> {}));
        steps.add(
            new Step(
                "answers",
                () -> check(
                    "whats_this_10_items",
                    answered == BLOCKS.length + ITEMS.length,
                    answered + " of " + (BLOCKS.length + ITEMS.length) + " named correctly"),
                () -> true,
                1000,
                () -> {}));
        steps.add(new Step("gos", () -> {
            gosNotices = gosCount(m);
            chatAs(playerName(), "!gos recipes for steel ingot");
        }, () -> gosCount(m) > gosNotices, GOS_MS, () -> {}));
        steps.add(new Step("gos answered", () -> {
            List<JsonObject> gos = gosNotices(m);
            String text = gos.isEmpty() ? "none" : str(gos.get(gos.size() - 1), "text");
            check("gos_from_the_player_answered", gos.size() > gosNotices, text);
            gosNotices = gos.size();
            chatAs("Mallory", "!gos recipes for steel ingot");
        }, () -> false, IGNORED_MS, () -> check("gos_from_others_ignored", gosCount(m) == gosNotices, "")));
        final int[] status = new int[1];
        steps.add(new Step("status", () -> {
            status[0] = m.link.sent("status");
            statusT0 = System.currentTimeMillis();
        }, () -> false, 5000, () -> {
            int sent = m.link.sent("status") - status[0];
            long ms = System.currentTimeMillis() - statusT0;
            check(
                "status_at_1_hz",
                m.link.connected() && sent >= ms / 1000 - 1 && sent <= ms / 1000 + 1,
                sent + " status messages in " + ms + " ms");
        }));
    }

    /** Presses What's this? (the key's code path) and waits for the core's answer to that report. */
    private void ask(ClientModule m, String name, String registry, int meta, boolean nbtExpected) {
        final long[] id = { 0 };
        final Feeds.Target[] seen = { null };
        steps.add(new Step(name, () -> {
            seen[0] = Targets.current(Minecraft.getMinecraft());
            id[0] = m.whatsThis();
        }, () -> answer(m, id[0]) != null, ANSWER_MS, () -> check(name, false, "no answer")));
        steps.add(new Step(name + " checked", () -> {
            JsonObject a = answer(m, id[0]);
            Feeds.Target t = seen[0];
            String key = str(a, "key");
            String got = str(a, "name");
            JsonObject item = t == null ? null : t.item;
            boolean right = item != null && registry.equals(str(item, "item"))
                && item.get("meta")
                    .getAsInt() == meta;
            boolean keyed = key != null && key.startsWith(registry + "@" + meta) && (nbtExpected == key.contains("#"));
            boolean named = got != null && t != null && got.equals(t.display);
            boolean ok = id[0] != 0 && right && keyed && named;
            if (ok) answered++;
            check(
                name,
                ok,
                "game " + (t == null ? "nothing" : t.kind + " " + (item == null ? "-" : Json.COMPACT.toJson(item)))
                    + " shown as "
                    + (t == null ? "-" : t.display)
                    + "; core "
                    + key
                    + " named "
                    + got
                    + ": "
                    + str(a, "text")
                    + (t == null || t == Feeds.Target.NONE ? " (" + mouse() + ")" : ""));
        }, () -> true, 1000, () -> {}));
    }

    private static JsonObject answer(ClientModule m, long id) {
        if (id == 0) return null;
        for (JsonObject n : m.recentNotices()) {
            JsonElement re = n.get("re");
            if ("answer".equals(str(n, "kind")) && re != null && re.isJsonPrimitive() && re.getAsLong() == id) return n;
        }
        return null;
    }

    private static List<JsonObject> gosNotices(ClientModule m) {
        List<JsonObject> out = new ArrayList<>();
        for (JsonObject n : m.recentNotices()) if ("gos".equals(str(n, "kind"))) out.add(n);
        return out;
    }

    private static int gosCount(ClientModule m) {
        return gosNotices(m).size();
    }

    private static String str(JsonObject o, String k) {
        if (o == null) return null;
        JsonElement e = o.get(k);
        return e == null || e.isJsonNull() ? null : e.getAsString();
    }

    private static String playerName() {
        return Minecraft.getMinecraft().thePlayer.getCommandSenderName();
    }

    // --- the world (the server's own thread)

    private static EntityPlayerMP serverPlayer() {
        List<?> players = MinecraftServer.getServer()
            .getConfigurationManager().playerEntityList;
        return players.isEmpty() ? null : (EntityPlayerMP) players.get(0);
    }

    private void place() {
        Minecraft.getMinecraft()
            .displayGuiScreen(null);
        serverTasks.add(() -> {
            EntityPlayerMP p = serverPlayer();
            if (p == null) return;
            World w = p.worldObj;
            int x = (int) Math.floor(p.posX), y = (int) Math.floor(p.posY) + 1, z = (int) Math.floor(p.posZ);
            int[][] at = new int[BLOCKS.length][];
            for (int i = 0; i < BLOCKS.length; i++) {
                at[i] = new int[] { x + OFFSETS[i][0], y, z + OFFSETS[i][1] };
                String name = BLOCKS[i][0];
                int meta = Integer.parseInt(BLOCKS[i][1]);
                try {
                    if (BLOCKS[i][2].equals("item")) {
                        Item item = (Item) GameData.getItemRegistry()
                            .getObject(name);
                        ItemStack s = new ItemStack(item, 1, meta);
                        item.onItemUse(s, p, w, at[i][0], at[i][1], at[i][2], 1, 0.5f, 0.5f, 0.5f);
                    } else {
                        Block b = (Block) GameData.getBlockRegistry()
                            .getObject(name);
                        w.setBlock(at[i][0], at[i][1], at[i][2], b, meta, 3);
                    }
                } catch (RuntimeException e) {
                    GregtecLink.LOG.error("client link harness: " + name + " not placed", e);
                }
            }
            placed = at;
        });
    }

    private boolean placedSeen() {
        int[][] at = placed;
        World w = Minecraft.getMinecraft().theWorld;
        if (at == null || w == null) return false;
        for (int[] p : at) if (w.isAirBlock(p[0], p[1], p[2])) return false;
        return true;
    }

    private void aim(int n) {
        Minecraft mc = Minecraft.getMinecraft();
        int[] p = placed[n];
        Vec3 eye = mc.thePlayer.getPosition(1.0f);
        double dx = p[0] + 0.5 - eye.xCoord, dy = p[1] + 0.5 - eye.yCoord, dz = p[2] + 0.5 - eye.zCoord;
        float yaw = (float) Math.toDegrees(Math.atan2(-dx, dz));
        float pitch = (float) -Math.toDegrees(Math.atan2(dy, Math.sqrt(dx * dx + dz * dz)));
        mc.thePlayer.rotationYaw = mc.thePlayer.prevRotationYaw = yaw;
        mc.thePlayer.rotationPitch = mc.thePlayer.prevRotationPitch = pitch;
        mc.entityRenderer.getMouseOver(1.0f);
    }

    private void chatAs(String name, String text) {
        serverTasks.add(() -> {
            EntityPlayerMP p = serverPlayer();
            if (p != null) p.addChatMessage(new ChatComponentTranslation("chat.type.text", name, text));
        });
    }

    private void requestRestart() {
        try {
            File dir = new File(gameDir, LINK_DIR);
            Files.write(new File(dir, RESTART).toPath(), "restart\n".getBytes(StandardCharsets.UTF_8));
        } catch (Exception e) {
            GregtecLink.LOG.error("client link harness: restart not requested", e);
        }
    }

    // --- the chest GUI

    private void openChest() {
        Minecraft mc = Minecraft.getMinecraft();
        InventoryBasic inv = new InventoryBasic("GregtecLink harness", true, 27);
        for (int i = 0; i < ITEMS.length; i++) {
            Item item = (Item) GameData.getItemRegistry()
                .getObject(ITEMS[i][0]);
            if (item == null) continue;
            ItemStack s = new ItemStack(item, 1, Integer.parseInt(ITEMS[i][1]));
            if (ITEMS[i][2] != null) {
                NBTTagCompound tag = new NBTTagCompound();
                tag.setDouble("charge", Double.parseDouble(ITEMS[i][2]));
                s.setTagCompound(tag);
            }
            inv.setInventorySlotContents(i, s);
        }
        chest = new GuiChest(mc.thePlayer.inventory, inv);
        mc.displayGuiScreen(chest);
    }

    private boolean guiReady() {
        return Minecraft.getMinecraft().currentScreen == chest && Targets.guiOrigin(chest) != null;
    }

    /** Moves the mouse onto the chest's slot {@code n}. */
    private void hover(int n) {
        Minecraft mc = Minecraft.getMinecraft();
        GuiContainer gui = chest;
        int[] origin = Targets.guiOrigin(gui);
        Slot s = gui.inventorySlots.getSlot(n);
        ScaledResolution sr = new ScaledResolution(mc, mc.displayWidth, mc.displayHeight);
        int scale = sr.getScaleFactor();
        int x = (origin[0] + s.xDisplayPosition + 8) * scale;
        int y = (origin[1] + s.yDisplayPosition + 8) * scale;
        cursor = new int[] { x, mc.displayHeight - y - 1 };
        flipped = false;
        Mouse.setCursorPosition(cursor[0], cursor[1]);
    }

    /**
     * Whether the mouse reads where {@link #hover} put it. LWJGL 2 takes {@code setCursorPosition}'s y from the bottom,
     * as {@code Mouse.getY()} gives it; lwjgl3ify (the Java 25 client) takes it from the top. So when the first move
     * reads back mirrored, the mouse is moved once more with y counted from the top.
     */
    private boolean mouseThere() {
        if (cursor == null) return false;
        if (Mouse.getX() == cursor[0] && Mouse.getY() == cursor[1]) return true;
        if (!flipped && Mouse.getX() == cursor[0]) {
            flipped = true;
            Mouse.setCursorPosition(cursor[0], Minecraft.getMinecraft().displayHeight - cursor[1]);
        }
        return false;
    }

    private String mouse() {
        Minecraft mc = Minecraft.getMinecraft();
        return "mouse at " + Mouse.getX()
            + ","
            + Mouse.getY()
            + (cursor == null ? "" : ", want " + cursor[0] + "," + cursor[1])
            + ", screen "
            + (mc.currentScreen == null ? "none"
                : mc.currentScreen.getClass()
                    .getSimpleName());
    }

    @SubscribeEvent
    public void onServerTick(TickEvent.ServerTickEvent e) {
        if (e.phase != TickEvent.Phase.END) return;
        Runnable r;
        while ((r = serverTasks.poll()) != null) r.run();
    }

    @SubscribeEvent
    public void onClientTick(TickEvent.ClientTickEvent e) {
        if (e.phase != TickEvent.Phase.END || finished) return;
        long now = System.currentTimeMillis();
        try {
            while (index < steps.size()) {
                Step s = steps.get(index);
                if (stepStart == 0) {
                    stepStart = now;
                    s.start.run();
                    return; // the world, the GUI and the link answer on later ticks
                }
                if (s.until.getAsBoolean()) {
                    index++;
                    stepStart = 0;
                    continue;
                }
                if (now - stepStart > s.timeoutMs) {
                    GregtecLink.LOG.warn("client link harness: step {} timed out", s.name);
                    s.onTimeout.run();
                    index++;
                    stepStart = 0;
                    continue;
                }
                return;
            }
        } catch (Throwable t) {
            GregtecLink.LOG.error("client link harness failed", t);
            check("client_link_harness_ran", false, Json.error(t));
        }
        finished = true;
        done.run();
    }
}
