package io.github.crazie450prog.gregteclink.export;

import java.io.File;

import gregtech.GTMod;
import io.github.crazie450prog.gregteclink.GregtecLink;

/**
 * Export module (GTNH-Export only, {@code export=true}). Plan §5.2 step 2: the GT dump, machine pass and manifest run
 * once the server has started (after GT's recipe unification). The NEI trigger and autoexport are PR 1b (V22).
 */
public final class ExportModule {

    private static File gameDir;

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
    }

    /** One-click export: opening Export's void world fires this on the integrated server. */
    public static void onServerStarted() {
        ExportRun.run(gameDir, AutoExport.requested() ? "autoexport" : "one_click");
    }

    /** For the harness, which owns the mode and the run. */
    public static ExportRun.Result runForHarness() {
        return ExportRun.run(gameDir, "harness");
    }
}
