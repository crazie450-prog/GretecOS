package io.github.crazie450prog.gregteclink.export;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** The .partial rename is retried, as Windows often still holds a just-written file. */
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
}
