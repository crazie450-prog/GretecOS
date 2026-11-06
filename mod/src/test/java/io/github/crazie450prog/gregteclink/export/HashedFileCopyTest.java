package io.github.crazie450prog.gregteclink.export;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import com.google.gson.JsonObject;

/** NEI's files are copied byte for byte; lines are counted by \n, as readers count them. */
class HashedFileCopyTest {

    @TempDir
    Path tmp;

    @Test
    void copiesBytesAndCountsLines() throws Exception {
        // Windows' println writes \r\n; a last line without \n is not counted.
        byte[] data = "{\"handler\":\"a\"}\r\n{\"handler\":\"b\"}\n{\"name\":\"Zinné\"}"
            .getBytes(StandardCharsets.UTF_8);
        PathGuard guard = new PathGuard(tmp, ExportRun.EXPORTS_DIR);
        HashedFile f = new HashedFile(guard, "run.partial", "nei/Assembler.jsonl");
        f.copy(new ByteArrayInputStream(data));
        f.close();

        Path written = guard.resolve("run.partial/nei/Assembler.jsonl");
        assertArrayEquals(data, Files.readAllBytes(written));
        JsonObject s = f.summary();
        assertEquals(
            Json.hex(
                MessageDigest.getInstance("SHA-256")
                    .digest(data)),
            s.get("sha256")
                .getAsString());
        assertEquals(
            data.length,
            s.get("bytes")
                .getAsLong());
        assertEquals(
            2,
            s.get("lines")
                .getAsLong());
        assertEquals(2, f.lines());
        assertEquals("nei/Assembler.jsonl", f.name);
    }

    @Test
    void copiesMoreThanOneBuffer() throws IOException {
        byte[] data = new byte[(1 << 16) * 3 + 7];
        for (int i = 0; i < data.length; i++) data[i] = (byte) (i % 100 == 99 ? '\n' : 'x');
        PathGuard guard = new PathGuard(tmp, ExportRun.EXPORTS_DIR);
        HashedFile f = new HashedFile(guard, "run.partial", "nei/big.jsonl");
        f.copy(new ByteArrayInputStream(data));
        f.close();
        assertEquals(data.length / 100, f.lines());
        assertArrayEquals(data, Files.readAllBytes(guard.resolve("run.partial/nei/big.jsonl")));
    }
}
