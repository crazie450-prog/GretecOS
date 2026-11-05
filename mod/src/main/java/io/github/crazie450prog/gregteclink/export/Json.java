package io.github.crazie450prog.gregteclink.export;

import java.util.Collection;

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
}
