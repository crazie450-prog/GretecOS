package io.github.crazie450prog.gregteclink.export;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;

/**
 * (e) of plan §5.2 step 2 (V22): with {@code -Dgregtec.autoexport=true} in GTNH-Export's JVM arguments (and
 * {@code export=true}), the client opens GregtecLink's own world from the main menu, the export runs, NEI's dump runs
 * once NEI has loaded, and the game quits. Launching Export (Prism's Launch button, or {@code prismlauncher --launch
 * GTNH-Export}) is then the one click.
 *
 * <p>
 * The world is rebuilt on every autoexport run, so a pack update never stops it at FML's "missing blocks" prompt.
 * GregtecLink deletes only {@code saves/gregteclink-autoexport}, only when its marker file is in it, and never follows
 * a link.
 */
public final class AutoExport {

    public static final String PROPERTY = "gregtec.autoexport";
    public static final String WORLD_FOLDER = "gregteclink-autoexport";
    public static final String WORLD_NAME = "GregtecLink autoexport";
    public static final String MARKER = "gregteclink-autoexport.txt";
    /** Superflat, one layer of bedrock: the harness's void-like world. */
    static final String GENERATOR = "2;7;1;";
    /** From entering the world until NEI has loaded. */
    static final long NEI_TIMEOUT_MS = 10 * 60_000L;
    /** From GregtecLink's preInit: the run is then written with what it has, and the game quits. */
    static final long DEADLINE_MS = 30 * 60_000L;
    private static final String MARKER_TEXT = "GregtecLink autoexport world (WO-1.1). GregtecLink deletes and recreates"
        + " this folder on every autoexport run: keep nothing here.\n";

    private AutoExport() {}

    public static boolean requested() {
        return Boolean.getBoolean(PROPERTY);
    }

    /**
     * Deletes the old autoexport world, if any, and recreates its folder with only the marker in it; Minecraft then
     * creates a new world there. Refuses (and changes nothing) if {@code saves} or the world folder is a link or
     * junction, if the world folder is not a folder, or if it has no marker. Returns the world folder.
     */
    static Path prepareWorld(File gameDir) throws IOException {
        PathGuard saves = new PathGuard(gameDir.toPath(), "saves");
        Path world = saves.resolve(WORLD_FOLDER);
        if (Files.exists(world, LinkOption.NOFOLLOW_LINKS)) {
            if (!Files.isDirectory(world, LinkOption.NOFOLLOW_LINKS) || !world.toRealPath()
                .equals(world)) {
                throw new SecurityException(world + " is not a plain folder: not deleted");
            }
            if (!Files.isRegularFile(world.resolve(MARKER), LinkOption.NOFOLLOW_LINKS)) {
                throw new SecurityException(world + " has no " + MARKER + ": not GregtecLink's world, not deleted");
            }
            ExportRun.deleteTree(world);
        }
        Files.createDirectory(world);
        Files.write(world.resolve(MARKER), MARKER_TEXT.getBytes(StandardCharsets.UTF_8));
        return world;
    }
}
