package io.github.crazie450prog.gregteclink.export;

import java.util.Collection;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonPrimitive;

/** Gson 2.2.4 (the version Minecraft 1.7.10 ships) helpers. Nulls are kept so positional arrays stay aligned. */
public final class Json {

    public static final Gson COMPACT = new GsonBuilder().disableHtmlEscaping()
        .serializeNulls()
        .create();
    public static final Gson PRETTY = new GsonBuilder().disableHtmlEscaping()
        .serializeNulls()
        .setPrettyPrinting()
        .create();

    /** A dotted Java name, then {@code @} and up to 8 lowercase hex digits (Integer.toHexString). */
    private static final Pattern IDENTITY_HASH = Pattern
        .compile("((?:[A-Za-z_$][\\w$]*+\\.)++[A-Za-z_$][\\w$]*+)@[0-9a-f]{1,8}+(?![\\w$])");
    private static final Map<String, Boolean> CLASSES = new ConcurrentHashMap<>();

    private Json() {}

    public static JsonElement str(String s) {
        return s == null ? JsonNull.INSTANCE : new JsonPrimitive(s);
    }

    public static JsonArray strings(Collection<String> values) {
        JsonArray a = new JsonArray();
        for (String v : values) a.add(str(v));
        return a;
    }

    public static JsonArray strings(String[] values) {
        JsonArray a = new JsonArray();
        for (String v : values) a.add(str(v));
        return a;
    }

    /** A positional int array; null stays null (the field is then omitted by the caller). */
    public static JsonArray ints(int[] values) {
        JsonArray a = new JsonArray();
        for (int v : values) a.add(new JsonPrimitive(v));
        return a;
    }

    public static String hex(byte[] bytes) {
        StringBuilder sb = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) {
            sb.append(Character.forDigit((b >> 4) & 0xF, 16))
                .append(Character.forDigit(b & 0xF, 16));
        }
        return sb.toString();
    }

    /** Short single-line description of a failure, for {"error": ...} fields. */
    public static String error(Throwable t) {
        String msg = t.getMessage();
        return t.getClass()
            .getName() + (msg == null ? "" : ": " + msg);
    }

    /**
     * Removes identity hashes ({@code "pkg.Name@1b6d3586"} becomes {@code "pkg.Name"}), as written by Object's
     * toString() and by ToStringBuilder; they change every run. Only a name that loads as a class counts, so an
     * ItemStack's {@code "1xitem.foo@3"} (count, unlocalized name, damage) is kept.
     */
    public static String stripIdentityHashes(String s) {
        if (s == null || s.indexOf('@') < 0) return s;
        Matcher m = IDENTITY_HASH.matcher(s);
        StringBuffer out = null;
        while (m.find()) {
            if (!isClass(m.group(1))) continue;
            if (out == null) out = new StringBuffer(s.length());
            m.appendReplacement(out, Matcher.quoteReplacement(m.group(1)));
        }
        if (out == null) return s;
        m.appendTail(out);
        return out.toString();
    }

    private static boolean isClass(String name) {
        Boolean known = CLASSES.get(name);
        if (known == null) {
            try {
                Class.forName(name, false, Json.class.getClassLoader());
                known = true;
            } catch (Throwable t) {
                known = false;
            }
            CLASSES.put(name, known);
        }
        return known;
    }
}
