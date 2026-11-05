package io.github.crazie450prog.gregteclink;

import java.io.File;

import net.minecraftforge.common.config.Configuration;

/**
 * Per-instance module switches, read from {@code config/gregteclink.cfg}.
 *
 * <p>
 * Every module defaults to off, and a missing file is never created: a jar without a config does nothing and writes
 * nothing. Export's config ({@code export=true}) is in Export's {@code sync_exclude}; Play gets listener and client
 * later through a script Clayton runs.
 */
public final class ModulesConfig {

    public static final String FILE_NAME = "gregteclink.cfg";
    public static final String CATEGORY = "modules";

    public final boolean export;
    public final boolean listener;
    public final boolean client;

    private ModulesConfig(boolean export, boolean listener, boolean client) {
        this.export = export;
        this.listener = listener;
        this.client = client;
    }

    public static ModulesConfig load(File configDir) {
        File file = new File(configDir, FILE_NAME);
        if (!file.isFile()) {
            return new ModulesConfig(false, false, false);
        }
        // Read only: never call save(), so the mod never rewrites the instance's config.
        Configuration cfg = new Configuration(file);
        return new ModulesConfig(
            cfg.get(CATEGORY, "export", false, "GTNH-Export only: recipe, machine and manifest dumps")
                .getBoolean(false),
            cfg.get(CATEGORY, "listener", false, "Play: machine place/break listener (WO-6.2)")
                .getBoolean(false),
            cfg.get(CATEGORY, "client", false, "Play: client link (WO-8.1)")
                .getBoolean(false));
    }

    @Override
    public String toString() {
        return "export=" + export + " listener=" + listener + " client=" + client;
    }
}
