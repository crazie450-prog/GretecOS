package io.github.crazie450prog.gregteclink.listener;

import java.io.File;
import java.nio.file.Path;

import net.minecraftforge.common.MinecraftForge;

import cpw.mods.fml.common.FMLCommonHandler;
import io.github.crazie450prog.gregteclink.GregtecLink;

/**
 * The listener module (WO-6.2, plan 5.4, D70, D260): GT machine place/break events for GOS's game watchers. On in Play
 * (and Lab, for the Lab check) through {@code config/gregteclink.cfg}; never with the export module, so never in
 * Export. It reads the world only (see {@link MachineListener}) and writes only its own events file.
 */
public final class ListenerModule {

    private static MachineListener listener;
    private static EventFile file;

    private ListenerModule() {}

    /** Registers the listener. Refused, with a warning, when the export module is on too (Export never listens). */
    public static void preInit(File gameDir, boolean exportEnabled) {
        if (exportEnabled) {
            GregtecLink.LOG.warn("listener module refused: the export module is on (the listener is off in Export)");
            return;
        }
        try {
            file = new EventFile(gameDir.toPath(), System.currentTimeMillis());
        } catch (Throwable t) {
            GregtecLink.LOG.error("listener module off: its events folder can't be used", t);
            return;
        }
        listener = new MachineListener(file);
        MinecraftForge.EVENT_BUS.register(listener);
        FMLCommonHandler.instance()
            .bus()
            .register(listener);
        GregtecLink.LOG.info("listener module on: GT machine place/break events go to {}", EventFile.DIR);
    }

    public static boolean active() {
        return listener != null;
    }

    /** The events file, for the harness; null when the module is off. */
    public static Path eventsPath() {
        try {
            return file == null ? null : file.path();
        } catch (Exception e) {
            return null;
        }
    }

    /** Events written this session (the harness's check). */
    public static int written() {
        return listener == null ? 0 : listener.written();
    }
}
