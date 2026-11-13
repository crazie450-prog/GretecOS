package io.github.crazie450prog.gregteclink.client;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

/** Every message fits the core's 4096-byte limit in UTF-8 bytes, not only in characters (review MEDIUM-1). */
class LinkMessagesTest {

    static String repeat(String s, int n) {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < n; i++) b.append(s);
        return b.toString();
    }

    static JsonObject obj(String s) {
        return new JsonParser().parse(s)
            .getAsJsonObject();
    }

    /** An item whose renamed display name is 1,400 CJK characters: under NBT_MAX characters, over 4 KB in UTF-8. */
    static JsonObject renamed() {
        JsonObject item = obj("{\"item\":\"IC2:itemBatREDischarged\",\"meta\":0}");
        JsonObject display = new JsonObject();
        display.addProperty("Name", repeat("漢", 1400));
        JsonObject nbt = new JsonObject();
        nbt.add("display", display);
        item.add("nbt", nbt);
        return item;
    }

    @Test
    void aMultiByteNbtIsDroppedSoTheMessageFits() {
        String nbtJson = renamed().get("nbt")
            .toString();
        assertTrue(nbtJson.length() <= LinkMessages.NBT_MAX, "the case itemRef lets through");
        String msg = LinkMessages.target(1, "key", null, "item", renamed(), null, repeat("Ж", 128));
        assertTrue(LinkMessages.bytes(msg) <= LinkMessages.BYTES_MAX, "bytes: " + LinkMessages.bytes(msg));
        JsonObject item = obj(msg).getAsJsonObject("item");
        assertFalse(item.has("nbt"));
        assertTrue(
            item.get("nbt_dropped")
                .getAsBoolean());
        assertEquals(
            "IC2:itemBatREDischarged",
            item.get("item")
                .getAsString());
        assertEquals(
            repeat("Ж", 128),
            obj(msg).get("display")
                .getAsString(),
            "the display name stays when dropping the NBT is enough");
    }

    @Test
    void aSmallNbtIsKept() {
        JsonObject item = obj("{\"item\":\"IC2:itemBatRE\",\"meta\":1,\"nbt\":{\"charge\":10000}}");
        String msg = LinkMessages.target(2, "key", null, "item", item, null, "RE-Battery");
        assertEquals(
            10000,
            obj(msg).getAsJsonObject("item")
                .getAsJsonObject("nbt")
                .get("charge")
                .getAsInt());
    }

    @Test
    void chatOfEscapedControlCharactersIsCutToFit() {
        String line = repeat("\u0001", LinkMessages.CHAT_MAX);
        String msg = LinkMessages.chat(line, "Clayton", line);
        assertTrue(LinkMessages.bytes(msg) <= LinkMessages.BYTES_MAX, "bytes: " + LinkMessages.bytes(msg));
        assertEquals(
            "Clayton",
            obj(msg).get("sender")
                .getAsString());
    }

    @Test
    void ordinaryChatIsUntouched() {
        String line = repeat("§", LinkMessages.CHAT_MAX);
        String msg = LinkMessages.chat(line, "Clayton", line);
        assertEquals(
            line,
            obj(msg).get("text")
                .getAsString());
    }
}
