package io.github.crazie450prog.gregteclink.export;

import java.io.File;
import java.util.concurrent.atomic.AtomicReference;

import cpw.mods.fml.common.FMLCommonHandler;
import gregtech.GTMod;
import io.github.crazie450prog.gregteclink.GregtecLink;

/**
 * Export module (GTNH-Export only, {@code export=true}). Plan §5.2 step 2: the GT dump, machine pass and item/fluid
 * tables run once the server has started (after GT's recipe unification). On a dedicated server the run finishes
 * there. On a client the run is handed to {@link ExportClient}, which adds NEI's dump on the client thread once NEI
 * has loaded and then finishes it.
 */
public final class ExportModule {

    private static File gameDir;
    /** A started run waiting for the client's NEI step; whoever takes it first finishes it. */
    private static final AtomicReference<ExportRun> PENDING = new AtomicReference<>();

    private ExportModule() {}

    /**
     * Runs after GT's preInit (required-after:gregtech). The one deliberate runtime change: recipes added from now on
     * record the mod that added them, as GT's own NEIRecipeOwner client option does. NEI in Export then shows "added
     * by" lines. Only with export=true, so never in Play.
     */
    public static void preInit(File dir) {
        gameDir = dir;
        GTMod.proxy.mNEIRecipeOwner = true;
        GregtecLink.LOG.info("export module on: recipe owner capture enabled");
        if (AutoExport.requested() && !client()) {
            GregtecLink.LOG.warn("-D{}=true ignored: autoexport needs a client", AutoExport.PROPERTY);
        }
    }

    /** Fires on the server thread when a world opens (one click, or the autoexport world). */
    public static void onServerStarted() {
        String mode = AutoExport.requested() && client() ? "autoexport" : "one_click";
        handOff(ExportRun.start(gameDir, mode));
    }

    /** For the server harness, which owns the mode and the run. */
    public static ExportRun.Result runForHarness() {
        return ExportRun.run(gameDir, "harness");
    }

    /** For the client harness: a run in mode "harness", handed to the client like any other. */
    public static void startForHarness() {
        handOff(ExportRun.start(gameDir, "harness"));
    }

    /** The world is closing: a run still waiting for NEI is finished without it. */
    public static void onServerStopping() {
        ExportRun run = PENDING.getAndSet(null);
        if (run == null) return;
        run.partErrors.put("nei", "world closed before NEI loaded");
        String auto = "off";
        if (AutoExport.requested()) {
            auto = "failed";
            run.partErrors.put("autoexport", "world closed before the NEI step");
        }
        run.finish("failed", auto);
    }

    private static void handOff(ExportRun run) {
        if (!client()) {
            run.finish(NeiTrigger.serverStatus(), "off");
            return;
        }
        ExportRun old = PENDING.getAndSet(run);
        if (old != null) { // Not expected: the last world's stop finishes its run.
            old.partErrors.put("nei", "superseded by run " + run.runId);
            old.finish("failed", "off");
        }
    }

    static ExportRun takePending() {
        return PENDING.getAndSet(null);
    }

    static boolean hasPending() {
        return PENDING.get() != null;
    }

    private static boolean client() {
        return FMLCommonHandler.instance()
            .getSide()
            .isClient();
    }
}
