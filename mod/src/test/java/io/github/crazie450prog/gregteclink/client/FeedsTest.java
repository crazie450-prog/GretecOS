package io.github.crazie450prog.gregteclink.client;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayList;
import java.util.List;

import org.junit.jupiter.api.Test;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

/** Each toggle turns its feed off; the status goes at most once a second; the key and the core's probe. */
class FeedsTest {

    final List<String[]> sent = new ArrayList<>();
    final List<String> shown = new ArrayList<>();
    boolean connected = true;

    Feeds feeds(String clientBlock) {
        ClientConfig cfg = ClientConfig.parse("client {\n" + clientBlock + "\n S:token_file=t\n}\n");
        return new Feeds(cfg, (type, text) -> {
            if (!connected) return false;
            sent.add(new String[] { type, text });
            return true;
        }, shown::add);
    }

    static final Feeds.Target STEEL = new Feeds.Target(
        "item",
        obj("{\"item\":\"gregtech:gt.metaitem.01\",\"meta\":11305}"),
        null,
        "Steel Ingot");

    static JsonObject obj(String s) {
        return new JsonParser().parse(s)
            .getAsJsonObject();
    }

    JsonObject last() {
        return obj(sent.get(sent.size() - 1)[1]);
    }

    @Test
    void everythingOnByDefault() {
        Feeds f = feeds("");
        f.tick(10_000, () -> "{\"type\":\"status\"}");
        f.tick(10_500, () -> "{\"type\":\"status\"}"); // within the second: not sent
        f.tick(11_000, () -> "{\"type\":\"status\"}");
        f.tick(12_000, () -> null); // no world: nothing
        assertEquals(2, sent.size());
        long id = f.whatsThis(() -> STEEL);
        assertEquals(1, id);
        JsonObject t = last();
        assertEquals(
            "target",
            t.get("type")
                .getAsString());
        assertEquals(
            "key",
            t.get("why")
                .getAsString());
        assertEquals(
            "gregtech:gt.metaitem.01",
            t.getAsJsonObject("item")
                .get("item")
                .getAsString());
        assertEquals(
            11305,
            t.getAsJsonObject("item")
                .get("meta")
                .getAsInt());
        f.chat("<Clayton> hi", "Clayton", "hi");
        assertEquals("chat", sent.get(sent.size() - 1)[0]);
        assertEquals("notice", f.receive(obj("{\"type\":\"notice\",\"text\":\"\\u00a7aOK\\nthen\"}"), () -> STEEL));
        assertEquals("[GOS] OK then", shown.get(0));
    }

    @Test
    void thePositionToggle() {
        Feeds f = feeds("B:position=false");
        f.tick(10_000, () -> "{\"type\":\"status\"}");
        assertTrue(sent.isEmpty());
    }

    @Test
    void theTargetToggleSendsNothingOnTheKeyAndOffToTheCoresProbe() {
        Feeds f = feeds("B:target=false");
        assertEquals(0, f.whatsThis(() -> STEEL));
        assertTrue(sent.isEmpty());
        assertEquals("probe", f.receive(obj("{\"type\":\"probe\",\"id\":42}"), () -> STEEL));
        JsonObject t = last();
        assertEquals(
            "off",
            t.get("kind")
                .getAsString());
        assertEquals(
            42,
            t.get("probe")
                .getAsLong());
        assertNull(t.get("item")); // nothing about what the player points at
    }

    @Test
    void theProbeIsAnsweredWithTheTarget() {
        Feeds f = feeds("");
        f.receive(obj("{\"type\":\"probe\",\"id\":3}"), () -> STEEL);
        JsonObject t = last();
        assertEquals(
            "probe",
            t.get("why")
                .getAsString());
        assertEquals(
            3,
            t.get("probe")
                .getAsLong());
        assertEquals(
            "Steel Ingot",
            t.get("display")
                .getAsString());
    }

    @Test
    void theChatToggle() {
        Feeds f = feeds("B:chat=false");
        f.chat("<Clayton> !gos hi", "Clayton", "!gos hi");
        assertTrue(sent.isEmpty());
    }

    @Test
    void theNoticesToggle() {
        Feeds f = feeds("B:notices=false");
        f.receive(obj("{\"type\":\"notice\",\"text\":\"hi\"}"), () -> STEEL);
        connected = false;
        assertEquals(0, f.whatsThis(() -> STEEL));
        assertTrue(shown.isEmpty());
    }

    @Test
    void theKeyWhileDisconnectedSaysSo() {
        Feeds f = feeds("");
        connected = false;
        assertEquals(0, f.whatsThis(() -> STEEL));
        assertEquals("[GOS] Gregtec OS isn't connected.", shown.get(0));
    }

    @Test
    void unknownOrMalformedMessagesAreIgnored() {
        Feeds f = feeds("");
        assertNull(f.receive(obj("{\"type\":\"exec\",\"cmd\":\"x\"}"), () -> STEEL));
        assertNull(f.receive(obj("{\"type\":\"probe\"}"), () -> STEEL));
        assertNull(f.receive(obj("{\"type\":\"notice\"}"), () -> STEEL));
        assertTrue(sent.isEmpty() && shown.isEmpty());
    }

    @Test
    void aLongNoticeIsCut() {
        String s = new String(new char[400]).replace('\0', 'x');
        assertEquals(
            Feeds.NOTICE_MAX,
            Feeds.clean(s)
                .length());
    }
}
