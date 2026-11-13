package io.github.crazie450prog.gregteclink.client;

import java.util.function.Supplier;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

/**
 * What the client module sends and shows, each part behind its toggle in {@link ClientConfig}: the 1 Hz status
 * ({@code position}), the hovered item or looked-at block ({@code target}), chat lines ({@code chat}) and GOS's local
 * chat notices ({@code notices}). No Minecraft classes, so the toggles are tested without a game.
 */
public final class Feeds {

    /** Where a message goes: the core link's {@link CoreLink#send}. */
    public interface Sender {

        boolean send(String type, String text);
    }

    /** A local chat line, shown only to the player (never sent to the server). */
    public interface Display {

        void show(String line);
    }

    /** What the player points at now. */
    public static final class Target {

        public static final Target NONE = new Target("none", null, null, null);

        public final String kind; // item, block or none
        public final JsonObject item;
        public final JsonObject block;
        public final String display; // the game's own name for it (data only)

        public Target(String kind, JsonObject item, JsonObject block, String display) {
            this.kind = kind;
            this.item = item;
            this.block = block;
            this.display = display;
        }
    }

    public static final long STATUS_EVERY_MS = 1000;
    public static final String PREFIX = "[GOS] ";
    static final int NOTICE_MAX = 300; // characters of one chat line shown

    private final ClientConfig cfg;
    private final Sender sender;
    private final Display display;
    private long lastStatusMs = Long.MIN_VALUE / 2;
    private long nextId = 1;

    public Feeds(ClientConfig cfg, Sender sender, Display display) {
        this.cfg = cfg;
        this.sender = sender;
        this.display = display;
    }

    /** Called every client tick: sends the status at most once a second, when there is one (in a world). */
    public void tick(long nowMs, Supplier<String> status) {
        if (!cfg.position || nowMs - lastStatusMs < STATUS_EVERY_MS) return;
        String s = status.get();
        if (s == null) return;
        lastStatusMs = nowMs;
        sender.send("status", s);
    }

    /**
     * The What's this? key: reports the target. Returns the report's id, or 0 when nothing was sent (the feed is off,
     * or the core isn't connected, which the player is told when notices are on).
     */
    public long whatsThis(Supplier<Target> target) {
        if (!cfg.target) return 0;
        Target t = target.get();
        long id = nextId++;
        if (sender.send("target", LinkMessages.target(id, "key", null, t.kind, t.item, t.block, t.display))) return id;
        if (cfg.notices) display.show(PREFIX + "Gregtec OS isn't connected.");
        return 0;
    }

    /**
     * The core asked what the player points at ("what's this?" said aloud). With the target feed off the answer is
     * {@code kind: off} and says nothing about it, so the core doesn't wait.
     */
    public void probe(long probe, Supplier<Target> target) {
        long id = nextId++;
        if (!cfg.target) {
            sender.send("target", LinkMessages.target(id, "probe", probe, "off", null, null, null));
            return;
        }
        Target t = target.get();
        sender.send("target", LinkMessages.target(id, "probe", probe, t.kind, t.item, t.block, t.display));
    }

    public void chat(String text, String from, String body, boolean singleplayer) {
        if (cfg.chat) sender.send("chat", LinkMessages.chat(text, from, body, singleplayer));
    }

    /**
     * One message from the core: {@code probe} is answered, {@code notice} shown (if notices are on). Returns the
     * message's type, or null for one that means nothing here.
     */
    public String receive(JsonObject msg, Supplier<Target> target) {
        String type = msg.get("type")
            .getAsString();
        if (type.equals("probe")) {
            JsonElement id = msg.get("id");
            if (id == null || !id.isJsonPrimitive()) return null;
            probe(id.getAsLong(), target);
            return type;
        }
        if (type.equals("notice")) {
            JsonElement text = msg.get("text");
            if (text == null || !text.isJsonPrimitive()) return null;
            if (cfg.notices) display.show(PREFIX + clean(text.getAsString()));
            return type;
        }
        return null;
    }

    /** One line, no formatting codes, at most {@link #NOTICE_MAX} characters. */
    static String clean(String s) {
        String out = s.replaceAll("§.", "")
            .replaceAll("[\\r\\n\\t]+", " ")
            .trim();
        return out.length() > NOTICE_MAX ? out.substring(0, NOTICE_MAX - 3) + "..." : out;
    }
}
