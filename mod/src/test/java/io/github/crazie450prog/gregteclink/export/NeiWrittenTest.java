package io.github.crazie450prog.gregteclink.export;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** Which of NEI's files the NEI step copies: regular .jsonl files written since the call started, sorted. */
class NeiWrittenTest {

    /** In the past, so a link made now (its own mtime) is newer than the start. */
    private static final long START = 1_700_000_000_000L;

    @TempDir
    Path tmp;

    @Test
    void onlyJsonlFilesChangedSinceTheStart() throws IOException {
        file("b.jsonl", START);
        file("a.jsonl", START + 5000);
        file("old.jsonl", START - 1);
        file("notes.txt", START + 5000);
        Files.createDirectories(tmp.resolve("dir.jsonl"));
        assertEquals(Arrays.asList("a.jsonl", "b.jsonl"), names(NeiTrigger.written(tmp, START)));
    }

    /** A link is never copied, even one to a fresh .jsonl file. */
    @Test
    void neverALink() throws IOException {
        Path target = file("real.jsonl", START + 1);
        try {
            Files.createSymbolicLink(tmp.resolve("link.jsonl"), target);
        } catch (UnsupportedOperationException | IOException e) {
            assumeTrue(false, "no symlinks here");
        }
        assertEquals(Arrays.asList("real.jsonl"), names(NeiTrigger.written(tmp, START)));
    }

    private Path file(String name, long mtime) throws IOException {
        Path p = Files.write(tmp.resolve(name), new byte[] { '{', '}', '\n' });
        Files.setLastModifiedTime(p, FileTime.fromMillis(mtime));
        return p;
    }

    private static List<String> names(List<Path> paths) {
        List<String> out = new ArrayList<>();
        for (Path p : paths) out.add(
            p.getFileName()
                .toString());
        return out;
    }
}
