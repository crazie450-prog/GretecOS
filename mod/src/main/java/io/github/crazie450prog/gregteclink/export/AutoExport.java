package io.github.crazie450prog.gregteclink.export;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;

import io.github.crazie450prog.gregteclink.GregtecLink;

/**
 * (e) of plan §5.2 step 2 (V22): with {@code -Dgregtec.autoexport=true} in GTNH-Export's JVM arguments (and
 * {@code export=true}), the client opens GregtecLink's own world from the main menu, the export runs, NEI's dump runs
 * once NEI has loaded, and the game quits. Launching Export (Prism's Launch button, or {@code prismlauncher --launch
 * GTNH-Export}) is then the one click.
 *
 * <p>
 * The world is rebuilt on every autoexport run, so a pack update never stops it at FML's "missing blocks" prompt.
 * GregtecLink deletes only {@code saves/gregteclink-autoexport} and its own moved-aside copies, only when its marker
 * file is in them, and never follows a link.
 */
public final class AutoExport {

    public static final String PROPERTY = "gregtec.autoexport";
    public static final String WORLD_FOLDER = "gregteclink-autoexport";
    public static final String WORLD_NAME = "GregtecLink autoexport";
    public static final String MARKER = "gregteclink-autoexport.txt";
    /** An old world is renamed to {@code gregteclink-autoexport.old-<epoch ms>} before it is deleted. */
    public static final String OLD_PREFIX = WORLD_FOLDER + ".old-";
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

    /** One delete step: {@link ExportRun#deleteTree} in the game; the tests make one fail partway. */
    interface Delete {

        void apply(Path p) throws IOException;
    }

    /**
     * Moves the old autoexport world aside, if there is one, deletes it and any copy an earlier run left, and recreates
     * the world folder with only the marker in it; Minecraft then creates a new world there. Refuses (and changes
     * nothing) if {@code saves} or the world folder is a link or junction, if the world folder is not a folder, or if
     * it has no marker. Returns the world folder.
     */
    static Path prepareWorld(File gameDir) throws IOException, InterruptedException {
        return prepareWorld(gameDir, ExportRun::deleteTree, ExportRun.RENAME_BACKOFF_MS);
    }

    static Path prepareWorld(File gameDir, Delete delete, long backoffMs) throws IOException, InterruptedException {
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
            // One atomic rename (retried: Windows may still hold a file in it), so the marker moves with the old
            // world. A delete that fails later then leaves a marked copy that the next run deletes, never a folder at
            // this name without its marker, which would be refused from then on.
            long id = System.currentTimeMillis();
            while (Files.exists(saves.resolve(OLD_PREFIX + id), LinkOption.NOFOLLOW_LINKS)) id++;
            ExportRun.rename(world, saves.resolve(OLD_PREFIX + id), backoffMs);
        }
        deleteOldCopies(saves.root(), delete, backoffMs);
        Files.createDirectory(world);
        Files.write(world.resolve(MARKER), MARKER_TEXT.getBytes(StandardCharsets.UTF_8));
        return world;
    }

    /**
     * Deletes every marked plain folder {@code gregteclink-autoexport.old-*} in {@code saves}, the marker last, each in
     * up to three tries. A failed delete is logged, not thrown: what can't be deleted stays, with its marker unless
     * only the empty folder itself was left.
     */
    private static void deleteOldCopies(Path saves, Delete delete, long backoffMs) throws InterruptedException {
        try (DirectoryStream<Path> s = Files.newDirectoryStream(saves, OLD_PREFIX + "*")) {
            for (Path old : s) {
                if (!Files.isDirectory(old, LinkOption.NOFOLLOW_LINKS) || !old.toRealPath()
                    .equals(old) || !Files.isRegularFile(old.resolve(MARKER), LinkOption.NOFOLLOW_LINKS)) continue;
                for (int attempt = 1;; attempt++) {
                    try {
                        deleteMarkerLast(old, delete);
                        break;
                    } catch (IOException e) {
                        if (attempt >= ExportRun.RENAME_ATTEMPTS) {
                            GregtecLink.LOG.warn("autoexport: old world {} not deleted: {}", old, e.toString());
                            break;
                        }
                        Thread.sleep(backoffMs << (attempt - 1));
                    }
                }
            }
        } catch (IOException e) {
            GregtecLink.LOG.warn("autoexport: old worlds not deleted: {}", e.toString());
        }
    }

    /**
     * Everything in {@code dir} but the marker, then the folder with the marker. A child that can't be deleted doesn't
     * stop the others (listing order is the file system's); the first failure is thrown after them.
     */
    private static void deleteMarkerLast(Path dir, Delete delete) throws IOException {
        IOException first = null;
        try (DirectoryStream<Path> children = Files.newDirectoryStream(dir)) {
            for (Path c : children) {
                if (c.getFileName()
                    .toString()
                    .equals(MARKER)) continue;
                try {
                    delete.apply(c);
                } catch (IOException e) {
                    if (first == null) first = e;
                }
            }
        }
        if (first != null) throw first;
        delete.apply(dir);
    }
}
