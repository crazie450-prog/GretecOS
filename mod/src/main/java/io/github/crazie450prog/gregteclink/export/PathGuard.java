package io.github.crazie450prog.gregteclink.export;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Every file GregtecLink writes goes through one of these. A path is accepted only if it is relative, has no
 * {@code ..} part, and resolves (after following any links that already exist) under the guard's root.
 */
public final class PathGuard {

    private final Path root;

    /** Creates the root if needed, then pins it to its real path. */
    public PathGuard(Path root) throws IOException {
        Files.createDirectories(root);
        this.root = root.toRealPath();
    }

    public Path root() {
        return root;
    }

    /** Resolves {@code relative} under the root, or throws {@link SecurityException}. */
    public Path resolve(String relative) throws IOException {
        if (relative == null || relative.isEmpty()) {
            throw new SecurityException("empty path");
        }
        Path rel = root.getFileSystem()
            .getPath(relative);
        if (rel.isAbsolute() || rel.getRoot() != null) {
            throw new SecurityException("absolute path refused: " + relative);
        }
        for (Path part : rel) {
            String p = part.toString();
            if (p.equals("..") || p.equals(".") || p.isEmpty()) {
                throw new SecurityException("path part refused: " + relative);
            }
        }
        Path target = root.resolve(rel)
            .normalize();
        if (!target.startsWith(root) || target.equals(root)) {
            throw new SecurityException("outside " + root + ": " + relative);
        }
        // The deepest existing ancestor must still be under the root once links are followed.
        Path existing = target;
        while (existing != null && !Files.exists(existing)) {
            existing = existing.getParent();
        }
        if (existing == null || !existing.toRealPath()
            .startsWith(root)) {
            throw new SecurityException("resolves outside " + root + ": " + relative);
        }
        return target;
    }
}
