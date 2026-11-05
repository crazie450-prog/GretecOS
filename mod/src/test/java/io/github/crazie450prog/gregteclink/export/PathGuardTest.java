package io.github.crazie450prog.gregteclink.export;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class PathGuardTest {

    @TempDir
    Path tmp;

    @Test
    void acceptsAPathUnderTheRoot() throws IOException {
        PathGuard g = new PathGuard(tmp.resolve("gregteclink/exports"));
        Path p = g.resolve("run1.partial/gt_recipes.jsonl");
        assertEquals(
            g.root()
                .resolve("run1.partial")
                .resolve("gt_recipes.jsonl"),
            p);
    }

    @Test
    void createsAndPinsTheRoot() throws IOException {
        PathGuard g = new PathGuard(tmp.resolve("a/b"));
        assertEquals(
            tmp.resolve("a/b")
                .toRealPath(),
            g.root());
    }

    @Test
    void refusesParentParts() throws IOException {
        PathGuard g = new PathGuard(tmp.resolve("exports"));
        assertThrows(SecurityException.class, () -> g.resolve("../escape.txt"));
        assertThrows(SecurityException.class, () -> g.resolve("run/../../escape.txt"));
        assertThrows(SecurityException.class, () -> g.resolve("./x"));
    }

    @Test
    void refusesAbsoluteAndEmptyPaths() throws IOException {
        PathGuard g = new PathGuard(tmp.resolve("exports"));
        assertThrows(
            SecurityException.class,
            () -> g.resolve(
                tmp.resolve("other")
                    .toAbsolutePath()
                    .toString()));
        assertThrows(SecurityException.class, () -> g.resolve(""));
        assertThrows(SecurityException.class, () -> g.resolve(null));
    }

    @Test
    void refusesALinkThatLeadsOutside() throws IOException {
        PathGuard g = new PathGuard(tmp.resolve("exports"));
        Path outside = Files.createDirectories(tmp.resolve("outside"));
        try {
            Files.createSymbolicLink(
                g.root()
                    .resolve("link"),
                outside);
        } catch (UnsupportedOperationException | IOException e) {
            assumeTrue(false, "symlinks not available here: " + e);
        }
        assertThrows(SecurityException.class, () -> g.resolve("link/file.txt"));
    }
}
