package io.github.crazie450prog.gregteclink.client;

import java.util.Map;

import net.minecraft.item.ItemStack;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import io.github.crazie450prog.gregteclink.export.Json;
import io.github.crazie450prog.gregteclink.export.StackJson;

/**
 * The messages the client module sends to the core ({@code gregteclink-client/1},
 * docs/contracts/gregteclink-client-v1.md). Items and blocks are registry name + meta (+ NBT), never numeric IDs: NBT
 * goes as {@link StackJson#nbt} gives it, minus the numeric {@code id} of every compound that also names its item
 * ({@code __item}), the same step as the core's {@code normalize_nbt}, so the core hashes it as it hashed the export.
 */
public final class LinkMessages {

    public static final int VERSION = 1;
    /** Characters of NBT JSON sent with an item; the core reads messages of at most 4096 bytes. */
    static final int NBT_MAX = 2048;
    static final int CHAT_MAX = 500; // characters of a chat line and of its body: both fit in 4 KB as UTF-8
    static final int DISPLAY_MAX = 128;

    private LinkMessages() {}

    public static String hello(String player, String modVersion, ClientConfig cfg) {
        JsonObject o = typed("hello");
        o.addProperty("player", player);
        o.addProperty("mod_version", modVersion);
        JsonObject feeds = new JsonObject();
        feeds.addProperty("position", cfg.position);
        feeds.addProperty("target", cfg.target);
        feeds.addProperty("chat", cfg.chat);
        feeds.addProperty("notices", cfg.notices);
        o.add("feeds", feeds);
        return Json.COMPACT.toJson(o);
    }

    /** Position (blocks, one decimal) and dimension, once a second; {@code paused}: the game is paused (V29). */
    public static String status(int dim, String dimName, double x, double y, double z, boolean paused) {
        JsonObject o = typed("status");
        o.addProperty("dim", dim);
        o.add("dim_name", Json.str(dimName));
        o.addProperty("x", Math.round(x * 10) / 10.0);
        o.addProperty("y", Math.round(y * 10) / 10.0);
        o.addProperty("z", Math.round(z * 10) / 10.0);
        o.addProperty("paused", paused);
        return Json.COMPACT.toJson(o);
    }

    /**
     * What the player points at. {@code why}: {@code key} (the What's this? key) or {@code probe} (the core asked;
     * {@code probe} is its id). {@code kind}: {@code item} (hovered in a GUI or NEI), {@code block} (looked at; its
     * pick item in {@code item}), {@code none} or {@code off} (the target feed is off). {@code id} numbers this
     * client's reports, so the answer names the one it answers.
     */
    public static String target(long id, String why, Long probe, String kind, JsonObject item, JsonObject block,
        String display) {
        JsonObject o = typed("target");
        o.addProperty("id", id);
        o.addProperty("why", why);
        if (probe != null) o.addProperty("probe", probe);
        o.addProperty("kind", kind);
        if (item != null) o.add("item", item);
        if (block != null) o.add("block", block);
        if (display != null) o.addProperty("display", cut(display, DISPLAY_MAX));
        return Json.COMPACT.toJson(o);
    }

    /** {@code sender} and {@code body} only when the line is a player's chat message (vanilla's chat.type.text). */
    public static String chat(String text, String sender, String body) {
        JsonObject o = typed("chat");
        o.addProperty("text", cut(text, CHAT_MAX));
        if (sender != null) o.addProperty("sender", cut(sender, 32));
        if (body != null) o.addProperty("body", cut(body, CHAT_MAX));
        return Json.COMPACT.toJson(o);
    }

    /** {"item","meta","nbt"?} of a stack; null for no stack or an unregistered item. */
    public static JsonObject itemRef(ItemStack stack) {
        if (stack == null || stack.getItem() == null) return null;
        String name = StackJson.itemName(stack.getItem());
        if (name == null) return null;
        JsonObject o = new JsonObject();
        o.addProperty("item", name);
        o.addProperty("meta", stack.getItemDamage());
        if (stack.getTagCompound() != null && !stack.getTagCompound()
            .hasNoTags()) {
            JsonElement nbt = withoutIds(StackJson.nbt(stack.getTagCompound()));
            // The core takes messages of at most 4 KB: a bigger NBT (a filled backpack) is left out and the
            // core answers for the plain item.
            if (Json.COMPACT.toJson(nbt)
                .length() <= NBT_MAX) o.add("nbt", nbt);
            else o.addProperty("nbt_dropped", true);
        }
        return o;
    }

    public static JsonObject blockRef(String registry, int meta, int x, int y, int z, int dim) {
        JsonObject o = new JsonObject();
        o.addProperty("block", registry);
        o.addProperty("meta", meta);
        o.addProperty("x", x);
        o.addProperty("y", y);
        o.addProperty("z", z);
        o.addProperty("dim", dim);
        return o;
    }

    /** A copy without the numeric {@code id} of each compound that has {@code __item} (its registry name). */
    static JsonElement withoutIds(JsonElement e) {
        if (e.isJsonObject()) {
            JsonObject in = e.getAsJsonObject();
            JsonObject out = new JsonObject();
            boolean named = in.has("__item");
            for (Map.Entry<String, JsonElement> m : in.entrySet()) {
                if (named && m.getKey()
                    .equals("id")) continue;
                out.add(m.getKey(), withoutIds(m.getValue()));
            }
            return out;
        }
        if (e.isJsonArray()) {
            com.google.gson.JsonArray out = new com.google.gson.JsonArray();
            for (JsonElement v : e.getAsJsonArray()) out.add(withoutIds(v));
            return out;
        }
        return e;
    }

    /** The core's message, or null when it isn't a JSON object with a string {@code type}. */
    public static JsonObject parse(String text) {
        try {
            JsonElement e = new JsonParser().parse(text);
            if (!e.isJsonObject()) return null;
            JsonObject o = e.getAsJsonObject();
            JsonElement t = o.get("type");
            return t != null && t.isJsonPrimitive()
                && t.getAsJsonPrimitive()
                    .isString() ? o : null;
        } catch (RuntimeException ex) {
            return null;
        }
    }

    static String cut(String s, int max) {
        return s.length() <= max ? s : s.substring(0, max);
    }

    private static JsonObject typed(String type) {
        JsonObject o = new JsonObject();
        o.addProperty("type", type);
        o.addProperty("v", VERSION);
        return o;
    }
}
