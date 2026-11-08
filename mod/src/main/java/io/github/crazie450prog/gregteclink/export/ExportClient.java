package io.github.crazie450prog.gregteclink.export;

import java.io.File;

import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.GuiMainMenu;
import net.minecraft.util.ChatComponentText;
import net.minecraft.world.WorldSettings;
import net.minecraft.world.WorldType;
import net.minecraftforge.client.event.GuiOpenEvent;
import net.minecraftforge.common.MinecraftForge;

import cpw.mods.fml.common.FMLCommonHandler;
import cpw.mods.fml.common.eventhandler.SubscribeEvent;
import cpw.mods.fml.common.gameevent.TickEvent;
import io.github.crazie450prog.gregteclink.GregtecLink;
import io.github.crazie450prog.gregteclink.harness.Harness;

/**
 * The export module's client side (client only, {@code export=true}). On the client tick: with autoexport, opens
 * GregtecLink's world from the main menu; in any world with a run waiting ({@link ExportModule}), waits for NEI, runs
 * the NEI step and then the crop step ({@link CropTrigger}), finishes the run; with autoexport, then quits. Without
 * autoexport (a world opened by hand) it posts two
 * local chat lines and stays in the world.
 */
public final class ExportClient {

    /**
     * With autoexport and no {@code GuiMainMenu} this long after the first client tick (another first screen in the
     * pack), the world is opened from whatever screen is up.
     */
    static final long MENU_GRACE_MS = 30_000L;

    private final File gameDir;
    private final boolean auto = AutoExport.requested();
    private final long startMs = System.currentTimeMillis();
    private long firstTickMs;
    private boolean menuSeen;
    private boolean launched;
    /** The game has left the world to quit; it shuts down on the next tick. */
    private boolean quitting;
    private boolean shutDown;
    private NeiWait wait;
    private long inWorldMs;

    private ExportClient(File gameDir) {
        this.gameDir = gameDir;
    }

    public static void register(File gameDir) {
        ExportClient c = new ExportClient(gameDir);
        FMLCommonHandler.instance()
            .bus()
            .register(c);
        MinecraftForge.EVENT_BUS.register(c);
        if (c.auto)
            GregtecLink.LOG.info("autoexport on: GregtecLink opens {} from the main menu", AutoExport.WORLD_FOLDER);
    }

    /**
     * Normal priority, so it sees the vanilla menu before Custom Main Menu (in GTNH) swaps it at LOWEST priority. The
     * world is launched on the next tick, outside the menu's own setup.
     */
    @SubscribeEvent
    public void onGuiOpen(GuiOpenEvent e) {
        if (e.gui instanceof GuiMainMenu) menuSeen = true;
    }

    @SubscribeEvent
    public void onClientTick(TickEvent.ClientTickEvent e) {
        if (e.phase != TickEvent.Phase.END || shutDown) return;
        Minecraft mc = Minecraft.getMinecraft();
        if (quitting) {
            shutDown(mc);
            return;
        }
        long now = System.currentTimeMillis();
        if (auto && now - startMs >= AutoExport.DEADLINE_MS) {
            deadline(mc);
            return;
        }
        if (auto && !launched && mc.theWorld == null) {
            // Client ticks start once the game has finished loading.
            if (firstTickMs == 0) firstTickMs = now;
            if (!menuSeen && now - firstTickMs >= MENU_GRACE_MS) {
                GregtecLink.LOG.warn(
                    "autoexport: no main menu {} s after loading; opening the world from {}",
                    MENU_GRACE_MS / 1000,
                    mc.currentScreen == null ? "no screen"
                        : mc.currentScreen.getClass()
                            .getName());
                menuSeen = true;
            }
            if (menuSeen) launch(mc);
            return;
        }
        if (mc.theWorld == null || mc.thePlayer == null || !ExportModule.hasPending()) {
            wait = null;
            return;
        }
        if (wait == null) {
            wait = new NeiWait(now, AutoExport.NEI_TIMEOUT_MS);
            inWorldMs = now;
        }
        if (!NeiTrigger.writerPresent()) {
            ExportRun run = ExportModule.takePending();
            if (run != null) done(mc, run, run.finish("absent", auto ? "ok" : "off"));
            return;
        }
        switch (wait.tick(NeiTrigger.ready(), now)) {
            case ANNOUNCE:
                chat(mc, "GregtecLink: exporting NEI's recipes; the game freezes for a while.");
                break;
            case DUMP: {
                ExportRun run = ExportModule.takePending();
                if (run == null) break;
                run.durations.put("nei_wait", now - inWorldMs);
                if (Harness.clientRunActive()) Harness.beforeNeiStep(gameDir);
                String nei = NeiTrigger.dump(run);
                if (Harness.clientRunActive()) Harness.afterNeiStep();
                run.crops = CropTrigger.dump(run);
                done(mc, run, run.finish(nei, auto ? "ok" : "off"));
                break;
            }
            case TIMEOUT: {
                ExportRun run = ExportModule.takePending();
                if (run == null) break;
                run.durations.put("nei_wait", now - inWorldMs);
                run.partErrors
                    .put("nei", "NEI not ready " + AutoExport.NEI_TIMEOUT_MS / 60_000 + " min after the world opened");
                done(mc, run, run.finish("failed", auto ? "ok" : "off"));
                break;
            }
            default:
                break;
        }
    }

    private void launch(Minecraft mc) {
        launched = true;
        try {
            AutoExport.prepareWorld(gameDir);
        } catch (Throwable t) {
            GregtecLink.LOG.error("autoexport: no run: world not prepared; quitting", t);
            quit(mc, null);
            return;
        }
        GregtecLink.LOG.info(
            "autoexport: main menu after {} ms; opening {}",
            System.currentTimeMillis() - startMs,
            AutoExport.WORLD_FOLDER);
        WorldSettings ws = new WorldSettings(0L, WorldSettings.GameType.CREATIVE, false, false, WorldType.FLAT);
        ws.func_82750_a(AutoExport.GENERATOR);
        ws.enableCommands();
        mc.launchIntegratedServer(AutoExport.WORLD_FOLDER, AutoExport.WORLD_NAME, ws);
    }

    /** The whole autoexport took too long: a waiting run is written with what it has, then the game quits. */
    private void deadline(Minecraft mc) {
        ExportRun run = ExportModule.takePending();
        ExportRun.Result r = null;
        if (run != null) {
            GregtecLink.LOG.error(
                "autoexport: not done {} min after start; writing the run and quitting",
                AutoExport.DEADLINE_MS / 60_000);
            String why = "autoexport deadline (" + AutoExport.DEADLINE_MS / 60_000 + " min) reached";
            run.partErrors.put("nei", why);
            run.partErrors.put("autoexport", why);
            r = run.finish("failed", "failed");
        } else {
            GregtecLink.LOG.error(
                "autoexport: no run: {} {} min after start; quitting",
                launched ? "the world was opened but no export started" : "the world was never opened",
                AutoExport.DEADLINE_MS / 60_000);
        }
        quit(mc, r);
    }

    private void done(Minecraft mc, ExportRun run, ExportRun.Result r) {
        chat(
            mc,
            "GregtecLink: export " + r.runId
                + " finished: complete="
                + r.complete
                + ", NEI "
                + run.parts.get("nei")
                + ", crops "
                + run.parts.get("crops")
                + (auto ? "; quitting." : "."));
        if (auto || Harness.clientRunActive()) quit(mc, r);
    }

    /**
     * Quits in two steps. This tick, the game leaves the world as "Save and Quit to Title" does, and its
     * {@code loadWorld(null)} waits until the integrated server has stopped and saved. The next tick,
     * {@link #shutDown}. Calling {@code Minecraft.shutdown()} from inside the world would not wait: once the game has
     * stopped running, Forge's wait for the server throws before it starts, and the JVM can exit while the server
     * thread is still saving.
     */
    private void quit(Minecraft mc, ExportRun.Result r) {
        quitting = true;
        if (Harness.clientRunActive()) Harness.onClientFinished(gameDir, r);
        if (mc.theWorld == null) return;
        try {
            mc.theWorld.sendQuittingDisconnectingPacket();
            mc.loadWorld(null);
            mc.displayGuiScreen(new GuiMainMenu());
        } catch (Throwable t) {
            GregtecLink.LOG.error("autoexport: leaving the world failed", t);
        }
    }

    private void shutDown(Minecraft mc) {
        shutDown = true;
        boolean serverStopped = mc.getIntegratedServer() == null;
        if (!serverStopped)
            GregtecLink.LOG.error("autoexport: the integrated server is still running; quitting anyway");
        if (Harness.clientRunActive()) Harness.onClientShutdown(gameDir, serverStopped);
        GregtecLink.LOG.info("autoexport: quitting");
        mc.shutdown();
    }

    private static void chat(Minecraft mc, String line) {
        GregtecLink.LOG.info(line);
        if (mc.ingameGUI != null) mc.ingameGUI.getChatGUI()
            .printChatMessage(new ChatComponentText(line));
    }
}
