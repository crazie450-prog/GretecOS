package io.github.crazie450prog.gregteclink.export;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;

/**
 * Every file GregtecLink writes goes through one of these. A path is accepted only if it is relative, has no
 * {@code ..} part, and resolves (after following any links that already exist) under the guard's root.
 */
public final class PathGuard {

    private final Path root;

    /**
     * Roots the guard at {@code base/sub} (e.g. the game dir and {@code gregteclink/exports}), creating it one folder
     * at a time. Refuses, before creating anything inside it, a part that is a link or junction: the root must be
     * exactly base's real path plus {@code sub}, so no write can land outside the game dir.
     */
    public PathGuard(Path base, String sub) throws IOException {
        Path dir = base.toRealPath();
        for (String part : sub.split("/")) {
            if (part.isEmpty() || part.equals(".") || part.equals("..")) {
                throw new SecurityException("root part refused: " + sub);
            }
            dir = dir.resolve(part);
            if (Files.notExists(dir, LinkOption.NOFOLLOW_LINKS)) Files.createDirectory(dir);
            if (!dir.toRealPath()
                .equals(dir)) {
                throw new SecurityException("link refused: " + dir + " leads to " + dir.toRealPath());
            }
        }
        this.root = dir;
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
