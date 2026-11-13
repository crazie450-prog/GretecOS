package io.github.crazie450prog.gregteclink;

import java.io.File;

import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

import cpw.mods.fml.common.FMLCommonHandler;
import cpw.mods.fml.common.Mod;
import cpw.mods.fml.common.event.FMLPreInitializationEvent;
import cpw.mods.fml.common.event.FMLServerStartedEvent;
import cpw.mods.fml.common.event.FMLServerStoppedEvent;
import cpw.mods.fml.common.event.FMLServerStoppingEvent;
import cpw.mods.fml.relauncher.Side;
import io.github.crazie450prog.gregteclink.client.ClientModule;
import io.github.crazie450prog.gregteclink.export.ExportClient;
import io.github.crazie450prog.gregteclink.export.ExportModule;
import io.github.crazie450prog.gregteclink.harness.Harness;
import io.github.crazie450prog.gregteclink.listener.ListenerModule;

/**
 * GregtecLink: Gregtec OS's game link. Three modules, switched per instance in {@code config/gregteclink.cfg}: export
 * (GTNH-Export only), listener (Play and Lab, WO-6.2; never with export) and client (WO-8.1). Adds no items, blocks,
 * mixins, recipes, commands or
 * packets.
 */
@Mod(
    modid = GregtecLink.MODID,
    version = Tags.VERSION,
    name = "GregtecLink",
    acceptedMinecraftVersions = "[1.7.10]",
    dependencies = "required-after:gregtech;after:NotEnoughItems",
    acceptableRemoteVersions = "*")
public class GregtecLink {

    public static final String MODID = "gregteclink";
    public static final Logger LOG = LogManager.getLogger(MODID);

    private static ModulesConfig modules;
    private static File gameDir;

    @Mod.EventHandler
    public void preInit(FMLPreInitializationEvent event) {
        File configDir = event.getModConfigurationDirectory();
        gameDir = configDir.getAbsoluteFile()
            .getParentFile();
        modules = ModulesConfig.load(configDir);
        LOG.info("GregtecLink {}: modules {}", Tags.VERSION, modules);
        if (modules.export) {
            ExportModule.preInit(gameDir);
            if (FMLCommonHandler.instance()
                .getSide() == Side.CLIENT) {
                ExportClient.register(gameDir);
            }
        }
        if (modules.listener) {
            ListenerModule.preInit(gameDir, modules.export);
        }
        if (modules.client && FMLCommonHandler.instance()
            .getSide() == Side.CLIENT) {
            ClientModule.preInit(gameDir, configDir);
        }
    }

    /** After every ServerStarting handler, so after GT's recipe unification. */
    @Mod.EventHandler
    public void serverStarted(FMLServerStartedEvent event) {
        if (Harness.requested()) {
            Harness.onServerStarted(gameDir, modules);
        } else if (modules.export) {
            ExportModule.onServerStarted();
        }
    }

    @Mod.EventHandler
    public void serverStopping(FMLServerStoppingEvent event) {
        if (modules.export) {
            ExportModule.onServerStopping();
        }
    }

    @Mod.EventHandler
    public void serverStopped(FMLServerStoppedEvent event) {
        Harness.onServerStopped();
    }
}
