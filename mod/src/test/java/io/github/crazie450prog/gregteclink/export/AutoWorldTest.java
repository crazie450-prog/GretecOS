package io.github.crazie450prog.gregteclink.export;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import java.io.IOException;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.Set;
import java.util.TreeSet;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** The autoexport world: GregtecLink deletes only its own marked folders, and never through a link. */
class AutoWorldTest {

    @TempDir
    Path tmp;

    @Test
    void createsTheWorldFolderWithItsMarker() throws Exception {
        Path game = Files.createDirectories(tmp.resolve("game"));
        Path world = AutoExport.prepareWorld(game.toFile());
        assertEquals(
            game.toRealPath()
                .resolve("saves/" + AutoExport.WORLD_FOLDER),
            world);
        assertEquals(names(world), new TreeSet<>(Arrays.asList(AutoExport.MARKER)));
    }

    @Test
    void rebuildsAMarkedWorldAndLeavesOtherWorldsAlone() throws Exception {
        Path game = Files.createDirectories(tmp.resolve("game"));
        Path old = Files.createDirectories(game.resolve("saves/" + AutoExport.WORLD_FOLDER + "/region"));
        Files.write(old.resolveSibling(AutoExport.MARKER), new byte[] { 1 });
        Files.write(old.resolve("r.0.0.mca"), new byte[] { 2 });
        Path decoy = Files.createDirectories(game.resolve("saves/survival"));
        Files.write(decoy.resolve(AutoExport.MARKER), new byte[] { 3 });
        Files.write(decoy.resolve("level.dat"), new byte[] { 4 });

        Path world = AutoExport.prepareWorld(game.toFile());

        assertEquals(names(world), new TreeSet<>(Arrays.asList(AutoExport.MARKER)));
        assertEquals(names(decoy), new TreeSet<>(Arrays.asList(AutoExport.MARKER, "level.dat")));
        assertArrayEquals(new byte[] { 4 }, Files.readAllBytes(decoy.resolve("level.dat")));
        assertEquals(names(game.resolve("saves")), new TreeSet<>(Arrays.asList(AutoExport.WORLD_FOLDER, "survival")));
    }

    /**
     * M2: a delete that fails partway (Windows: a scanner still holds a file) leaves a marked copy under another name,
     * never a world folder without its marker. The run goes on, and a later run deletes the copy.
     */
    @Test
    void aDeleteThatFailsPartwayNeverBlocksALaterRun() throws Exception {
        Path game = Files.createDirectories(tmp.resolve("game"));
        Path old = Files.createDirectories(game.resolve("saves/" + AutoExport.WORLD_FOLDER));
        Files.write(old.resolve(AutoExport.MARKER), new byte[] { 1 });
        // Before and after the marker in name order (NTFS lists a folder that way).
        Files.createDirectories(old.resolve("data"));
        Files.write(old.resolve("level.dat"), new byte[] { 2 });
        Files.write(
            Files.createDirectories(old.resolve("region"))
                .resolve("r.0.0.mca"),
            new byte[] { 3 });
        int[] tries = { 0 };
        AutoExport.Delete heldRegion = p -> {
            if (p.getFileName()
                .toString()
                .equals("region")) {
                tries[0]++;
                throw new IOException("held");
            }
            ExportRun.deleteTree(p);
        };

        Path world = AutoExport.prepareWorld(game.toFile(), heldRegion, 1);

        assertEquals(names(world), new TreeSet<>(Arrays.asList(AutoExport.MARKER)));
        assertEquals(3, tries[0]);
        Path copy = onlyOldCopy(game);
        assertEquals(names(copy), new TreeSet<>(Arrays.asList(AutoExport.MARKER, "region")));

        AutoExport.prepareWorld(game.toFile());

        assertEquals(names(game.resolve("saves")), new TreeSet<>(Arrays.asList(AutoExport.WORLD_FOLDER)));
    }

    /** Only marked plain folders are ever deleted, the moved-aside copies included. */
    @Test
    void leavesUnmarkedAndLinkedOldCopiesAlone() throws Exception {
        Path game = Files.createDirectories(tmp.resolve("game"));
        Path unmarked = Files.createDirectories(game.resolve("saves/" + AutoExport.OLD_PREFIX + "1"));
        Files.write(unmarked.resolve("level.dat"), new byte[] { 1 });
        Path outside = Files.createDirectories(tmp.resolve("outside"));
        Files.write(outside.resolve(AutoExport.MARKER), new byte[] { 2 });
        Files.write(outside.resolve("keep.txt"), new byte[] { 3 });
        boolean linked = link(game.resolve("saves/" + AutoExport.OLD_PREFIX + "2"), outside);

        AutoExport.prepareWorld(game.toFile());

        assertTrue(Files.isRegularFile(unmarked.resolve("level.dat")));
        assertTrue(Files.isRegularFile(outside.resolve("keep.txt")));
        if (linked) assertTrue(Files.isSymbolicLink(game.resolve("saves/" + AutoExport.OLD_PREFIX + "2")));
    }

    @Test
    void refusesAWorldWithoutTheMarker() throws Exception {
        Path game = Files.createDirectories(tmp.resolve("game"));
        Path world = Files.createDirectories(game.resolve("saves/" + AutoExport.WORLD_FOLDER));
        Files.write(world.resolve("level.dat"), new byte[] { 1 });
        assertThrows(SecurityException.class, () -> AutoExport.prepareWorld(game.toFile()));
        assertTrue(Files.isRegularFile(world.resolve("level.dat")));
        assertEquals(names(game.resolve("saves")), new TreeSet<>(Arrays.asList(AutoExport.WORLD_FOLDER)));
    }

    @Test
    void refusesAFileInThePlaceOfTheWorld() throws IOException {
        Path game = Files.createDirectories(tmp.resolve("game/saves"))
            .getParent();
        Files.write(game.resolve("saves/" + AutoExport.WORLD_FOLDER), new byte[] { 1 });
        assertThrows(SecurityException.class, () -> AutoExport.prepareWorld(game.toFile()));
    }

    /** A linked world folder, even one whose target has the marker: refused, and nothing is deleted through it. */
    @Test
    void refusesALinkedWorldFolder() throws IOException {
        Path game = Files.createDirectories(tmp.resolve("game/saves"))
            .getParent();
        Path outside = Files.createDirectories(tmp.resolve("outside"));
        Files.write(outside.resolve(AutoExport.MARKER), new byte[] { 1 });
        Files.write(outside.resolve("keep.txt"), new byte[] { 2 });
        assumeTrue(link(game.resolve("saves/" + AutoExport.WORLD_FOLDER), outside), "no symlinks here");
        assertThrows(SecurityException.class, () -> AutoExport.prepareWorld(game.toFile()));
        assertTrue(Files.isRegularFile(outside.resolve("keep.txt")));
    }

    @Test
    void refusesALinkedSavesFolder() throws IOException {
        Path game = Files.createDirectories(tmp.resolve("game"));
        Path outside = Files.createDirectories(tmp.resolve("outside"));
        assumeTrue(link(game.resolve("saves"), outside), "no symlinks here");
        assertThrows(SecurityException.class, () -> AutoExport.prepareWorld(game.toFile()));
        assertFalse(Files.exists(outside.resolve(AutoExport.WORLD_FOLDER)));
    }

    /** A link inside the old world is deleted itself; its target is never followed. */
    @Test
    void neverFollowsALinkInsideTheOldWorld() throws Exception {
        Path game = Files.createDirectories(tmp.resolve("game"));
        Path old = Files.createDirectories(game.resolve("saves/" + AutoExport.WORLD_FOLDER));
        Files.write(old.resolve(AutoExport.MARKER), new byte[] { 1 });
        Path outside = Files.createDirectories(tmp.resolve("outside"));
        Files.write(outside.resolve("keep.txt"), new byte[] { 2 });
        assumeTrue(link(old.resolve("linked"), outside), "no symlinks here");
        AutoExport.prepareWorld(game.toFile());
        assertTrue(Files.isRegularFile(outside.resolve("keep.txt")));
        assertFalse(Files.exists(old.resolve("linked")));
        assertEquals(names(game.resolve("saves")), new TreeSet<>(Arrays.asList(AutoExport.WORLD_FOLDER)));
    }

    private static Path onlyOldCopy(Path game) throws IOException {
        Path found = null;
        try (DirectoryStream<Path> s = Files.newDirectoryStream(game.resolve("saves"), AutoExport.OLD_PREFIX + "*")) {
            for (Path p : s) {
                assertEquals(null, found, "more than one old copy");
                found = p;
            }
        }
        assertTrue(found != null, "no old copy");
        return found;
    }

    private static Set<String> names(Path dir) throws IOException {
        Set<String> out = new TreeSet<>();
        try (DirectoryStream<Path> s = Files.newDirectoryStream(dir)) {
            for (Path p : s) out.add(
                p.getFileName()
                    .toString());
        }
        return out;
    }

    private static boolean link(Path link, Path target) {
        try {
            Files.createSymbolicLink(link, target);
            return true;
        } catch (UnsupportedOperationException | IOException e) {
            return false;
        }
    }
}
