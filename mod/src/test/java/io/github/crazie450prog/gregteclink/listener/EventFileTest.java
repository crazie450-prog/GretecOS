package io.github.crazie450prog.gregteclink.listener;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

/** The events file: its lines (contract gregteclink-events-v1), appends, rotation and path guard. */
class EventFileTest {

    @TempDir
    Path tmp;

    private static Pending.Event built(int id) {
        return new Pending.Event(Pending.BUILT, -1, 10, 64, -20, id, "gregtech:gt.blockmachines");
    }

    @Test
    void writesOneLinePerEventWithRegistryAndMeta() throws IOException {
        EventFile f = new EventFile(tmp, 1234L);
        f.write(built(1000), 5000L);
        f.write(new Pending.Event(Pending.REMOVED, 0, 1, 2, 3, 66, null), 6000L);
        List<String> lines = Files
            .readAllLines(tmp.resolve("gregteclink/events/machines.jsonl"), StandardCharsets.UTF_8);
        assertEquals(2, lines.size());
        JsonObject a = new JsonParser().parse(lines.get(0))
            .getAsJsonObject();
        assertEquals(
            "{\"v\":1,\"kind\":\"machine_built\",\"boot\":1234,\"seq\":1,\"ts_ms\":5000,\"dim\":-1,\"x\":10,\"y\":64,"
                + "\"z\":-20,\"mte_id\":1000,\"registry\":\"gregtech:gt.blockmachines\",\"meta\":1000}",
            lines.get(0));
        assertFalse(a.has("id"));
        JsonObject b = new JsonParser().parse(lines.get(1))
            .getAsJsonObject();
        assertEquals(
            2,
            b.get("seq")
                .getAsInt());
        assertTrue(
            b.get("registry")
                .isJsonNull());
    }

    @Test
    void appendsAcrossSessionsAndRotatesWhenLarge() throws IOException {
        new EventFile(tmp, 1L).write(built(1), 1L);
        new EventFile(tmp, 2L).write(built(2), 2L);
        Path file = tmp.resolve("gregteclink/events/machines.jsonl");
        assertEquals(
            2,
            Files.readAllLines(file, StandardCharsets.UTF_8)
                .size());
        byte[] big = new byte[(int) EventFile.ROTATE_BYTES + 1];
        Files.write(file, big);
        new EventFile(tmp, 3L).write(built(3), 3L);
        assertEquals(
            1,
            Files.readAllLines(file, StandardCharsets.UTF_8)
                .size());
        assertEquals(big.length, Files.size(tmp.resolve("gregteclink/events/machines.1.jsonl")));
    }

    @Test
    void refusesALinkedEventsFolder() throws IOException {
        Path outside = Files.createDirectories(tmp.resolve("outside"));
        Path game = Files.createDirectories(tmp.resolve("game/gregteclink"));
        try {
            Files.createSymbolicLink(game.resolve("events"), outside);
        } catch (UnsupportedOperationException | IOException e) {
            return; // no symlinks here (Windows without the privilege)
        }
        assertThrows(SecurityException.class, () -> new EventFile(tmp.resolve("game"), 1L));
    }
}
