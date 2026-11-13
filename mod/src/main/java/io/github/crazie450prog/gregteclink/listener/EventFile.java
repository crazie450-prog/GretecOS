package io.github.crazie450prog.gregteclink.listener;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;

import com.google.gson.JsonObject;

import io.github.crazie450prog.gregteclink.export.Json;
import io.github.crazie450prog.gregteclink.export.PathGuard;

/**
 * The listener's events, one JSON object per line, in {@code <gameDir>/gregteclink/events/machines.jsonl} (contract:
 * docs/contracts/gregteclink-events-v1.md, D260). GOS's game watchers tail it as they tail the logs, through
 * {@code safe_open}, and send its lines to the core.
 *
 * <p>
 * Every write goes through a {@link PathGuard} rooted at {@code gregteclink/events}: open, append one line, close, so
 * no handle is held between events. When the file is over {@link #ROTATE_BYTES} at start it is renamed to
 * {@code machines.1.jsonl} (replacing the older one), so it never grows without bound; the watchers see the new file
 * as a rotation, as with a log.
 */
public final class EventFile {

    public static final String DIR = "gregteclink/events";
    public static final String NAME = "machines.jsonl";
    public static final String OLD_NAME = "machines.1.jsonl";
    public static final int VERSION = 1;
    static final long ROTATE_BYTES = 1L << 20;

    private final PathGuard guard;
    private final long boot;
    private long seq;

    /** {@code boot} names this game session in every line (the watchers key a line on it and {@code seq}). */
    public EventFile(Path gameDir, long boot) throws IOException {
        this.guard = new PathGuard(gameDir, DIR);
        this.boot = boot;
        Path file = guard.resolve(NAME);
        if (Files.isRegularFile(file) && Files.size(file) > ROTATE_BYTES) {
            Files.move(file, guard.resolve(OLD_NAME), StandardCopyOption.REPLACE_EXISTING);
        }
    }

    public Path path() throws IOException {
        return guard.resolve(NAME);
    }

    /** The JSON line for one event (without the newline); {@code tsMs} is the wall clock in ms. */
    String line(Pending.Event e, long tsMs) {
        JsonObject o = new JsonObject();
        o.addProperty("v", VERSION);
        o.addProperty("kind", e.kind);
        o.addProperty("boot", boot);
        o.addProperty("seq", seq + 1);
        o.addProperty("ts_ms", tsMs);
        o.addProperty("dim", e.dim);
        o.addProperty("x", e.x);
        o.addProperty("y", e.y);
        o.addProperty("z", e.z);
        o.addProperty("mte_id", e.mteId);
        // The machine as an item: GT's machine block's registry name + meta, where meta is the mte id (as the
        // export's machines.jsonl keys it). Never a numeric block or item id (EndlessIDs).
        o.add("registry", Json.str(e.registry));
        o.addProperty("meta", e.mteId);
        return Json.COMPACT.toJson(o);
    }

    /** Appends one event. */
    public void write(Pending.Event e, long tsMs) throws IOException {
        String text = line(e, tsMs) + "\n";
        Files.write(
            guard.resolve(NAME),
            text.getBytes(StandardCharsets.UTF_8),
            StandardOpenOption.CREATE,
            StandardOpenOption.APPEND,
            StandardOpenOption.WRITE);
        seq++;
    }
}
