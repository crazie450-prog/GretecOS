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
 * the NEI step, finishes the run; with autoexport, then quits. Without autoexport (a world opened by hand) it posts two
 * local chat lines and stays in the world.
 */
public final class ExportClient {

    private final File gameDir;
    private final boolean auto = AutoExport.requested();
    private final long startMs = System.currentTimeMillis();
    private boolean menuSeen;
    private boolean launched;
    private boolean quitting;
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
        if (e.phase != TickEvent.Phase.END || quitting) return;
        Minecraft mc = Minecraft.getMinecraft();
        long now = System.currentTimeMillis();
        if (auto && now - startMs >= AutoExport.DEADLINE_MS) {
            deadline(mc);
            return;
        }
        if (auto && !launched && menuSeen && mc.theWorld == null) {
            launch(mc);
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
            GregtecLink.LOG.error("autoexport: world not prepared; quitting without an export", t);
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
        GregtecLink.LOG.error("autoexport: not done {} min after start; quitting", AutoExport.DEADLINE_MS / 60_000);
        ExportRun run = ExportModule.takePending();
        ExportRun.Result r = null;
        if (run != null) {
            String why = "autoexport deadline (" + AutoExport.DEADLINE_MS / 60_000 + " min) reached";
            run.partErrors.put("nei", why);
            run.partErrors.put("autoexport", why);
            r = run.finish("failed", "failed");
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
                + (auto ? "; quitting." : "."));
        if (auto || Harness.clientRunActive()) quit(mc, r);
    }

    private void quit(Minecraft mc, ExportRun.Result r) {
        quitting = true;
        if (Harness.clientRunActive()) Harness.onClientFinished(gameDir, r);
        mc.shutdown();
    }

    private static void chat(Minecraft mc, String line) {
        GregtecLink.LOG.info(line);
        if (mc.ingameGUI != null) mc.ingameGUI.getChatGUI()
            .printChatMessage(new ChatComponentText(line));
    }
}
