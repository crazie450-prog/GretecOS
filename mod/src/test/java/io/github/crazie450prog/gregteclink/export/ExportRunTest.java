package io.github.crazie450prog.gregteclink.export;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** The .partial rename is retried, as Windows often still holds a just-written file; old runs are pruned. */
class ExportRunTest {

    @TempDir
    Path tmp;

    @Test
    void renameTakesOneTryWhenNothingIsInTheWay() throws Exception {
        Path from = Files.createDirectories(tmp.resolve("run.partial"));
        assertEquals(1, ExportRun.rename(from, tmp.resolve("run"), 1));
        assertTrue(Files.isDirectory(tmp.resolve("run")));
    }

    @Test
    void renameRetriesUntilTheTargetIsFree() throws Exception {
        Path from = Files.createDirectories(tmp.resolve("run.partial"));
        Path to = Files.createDirectories(tmp.resolve("run"));
        // A non-empty target refuses the move, like a folder Windows still holds.
        Path blocker = Files.createFile(to.resolve("held"));
        Thread release = new Thread(() -> {
            try {
                Thread.sleep(200);
                Files.delete(blocker);
                Files.delete(to);
            } catch (Exception e) {
                throw new RuntimeException(e);
            }
        });
        release.start();
        int tries = ExportRun.rename(from, to, 600);
        release.join();
        assertEquals(2, tries);
        assertTrue(Files.isDirectory(to));
    }

    @Test
    void renameGivesUpAfterThreeTries() throws IOException {
        Path from = Files.createDirectories(tmp.resolve("run.partial"));
        Path to = Files.createDirectories(tmp.resolve("run"));
        Files.createFile(to.resolve("held"));
        assertThrows(IOException.class, () -> ExportRun.rename(from, to, 1));
        assertTrue(Files.isDirectory(from));
    }

    /** D98: the newest 10 complete runs stay; older complete runs and earlier .partial folders go. */
    @Test
    void pruneKeepsTheNewestCompleteRuns() throws IOException {
        PathGuard guard = new PathGuard(Files.createDirectories(tmp.resolve("game")), ExportRun.EXPORTS_DIR);
        Path root = guard.root();
        for (int i = 0; i < 12; i++) {
            // Names sort the other way round, so only created_utc can order them.
            run(root, "run-" + (char) ('z' - i), true, String.format("2026-11-%02dT00:00:00Z", i + 1));
        }
        run(root, "current", true, "2026-12-01T00:00:00Z");
        run(root, "failed", false, "2020-01-01T00:00:00Z");
        Files.createDirectories(root.resolve("no-manifest"));
        run(root, "stale.partial", true, "2026-12-02T00:00:00Z");
        run(root, "current.partial", true, "2026-12-02T00:00:00Z"); // never touched
        Path outside = Files.createDirectories(tmp.resolve("outside"));
        Files.write(outside.resolve("keep.txt"), new byte[] { 1 });
        boolean linked = link(root.resolve("run-z/link"), outside);

        List<String> deleted = ExportRun.prune(guard, "current", 10);

        // current and the 9 newest others (run-o .. run-w) stay; the oldest three go, and the link in run-z is
        // deleted without following it.
        assertEquals(new TreeSet<>(Arrays.asList("run-z", "run-y", "run-x", "stale.partial")), new TreeSet<>(deleted));
        Set<String> left = new TreeSet<>();
        try (DirectoryStream<Path> s = Files.newDirectoryStream(root)) {
            for (Path p : s) left.add(
                p.getFileName()
                    .toString());
        }
        Set<String> want = new TreeSet<>(
            Arrays.asList("current", "current.partial", "failed", "no-manifest", "run-w", "run-v", "run-u", "run-t"));
        for (char c = 'o'; c <= 's'; c++) want.add("run-" + c);
        assertEquals(want, left);
        if (linked) assertTrue(Files.isRegularFile(outside.resolve("keep.txt")));
    }

    private static void run(Path root, String name, boolean complete, String created) throws IOException {
        Path dir = Files.createDirectories(root.resolve(name));
        String manifest = "{\"format\":\"" + ExportRun.FORMAT
            + "\",\"complete\":"
            + complete
            + ",\"created_utc\":\""
            + created
            + "\"}";
        Files.write(dir.resolve("manifest.json"), manifest.getBytes(StandardCharsets.UTF_8));
        Files.write(dir.resolve("gt_recipes.jsonl"), "{}\n".getBytes(StandardCharsets.UTF_8));
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
