package io.github.crazie450prog.gregteclink.export;

import java.lang.reflect.Array;
import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.Collection;
import java.util.Map;
import java.util.TreeMap;

import net.minecraft.item.ItemStack;
import net.minecraftforge.fluids.Fluid;
import net.minecraftforge.fluids.FluidStack;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;

import gregtech.api.enums.Materials;

/**
 * Encodes recipe metadata values and special items, raw: numbers, booleans and strings as is; stacks as refs; enums by
 * name(); GT Materials by mName; arrays and collections as lists; anything else as its class plus its public
 * simple-typed fields and toString().
 */
final class MetaJson {

    private static final int MAX_DEPTH = 4;

    private final StackJson stacks;

    MetaJson(StackJson stacks) {
        this.stacks = stacks;
    }

    JsonElement encode(Object v) {
        return encode(v, 0);
    }

    private JsonElement encode(Object v, int depth) {
        if (v == null) return JsonNull.INSTANCE;
        if (v instanceof Boolean) return new JsonPrimitive((Boolean) v);
        if (v instanceof Double || v instanceof Float) {
            double d = ((Number) v).doubleValue();
            return Double.isNaN(d) || Double.isInfinite(d) ? new JsonPrimitive(v.toString())
                : new JsonPrimitive((Number) v);
        }
        if (v instanceof Number) return new JsonPrimitive((Number) v);
        if (v instanceof String) return new JsonPrimitive((String) v);
        if (v instanceof Character) return new JsonPrimitive(v.toString());
        if (v instanceof ItemStack) return stacks.item((ItemStack) v);
        if (v instanceof FluidStack) return stacks.fluid((FluidStack) v);
        if (v instanceof Fluid) {
            JsonObject o = new JsonObject();
            o.add("fluid", stacks.fluidRef((Fluid) v));
            return o;
        }
        if (v instanceof Materials) return new JsonPrimitive(((Materials) v).mName);
        if (v instanceof Enum) return new JsonPrimitive(((Enum<?>) v).name());
        if (depth >= MAX_DEPTH) return opaque(v, false);
        if (v.getClass()
            .isArray()) {
            JsonArray a = new JsonArray();
            int n = Array.getLength(v);
            for (int i = 0; i < n; i++) a.add(encode(Array.get(v, i), depth + 1));
            return a;
        }
        if (v instanceof Collection) {
            JsonArray a = new JsonArray();
            for (Object e : (Collection<?>) v) a.add(encode(e, depth + 1));
            return a;
        }
        if (v instanceof Map) {
            TreeMap<String, Object> sorted = new TreeMap<>();
            for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) {
                sorted.put(String.valueOf(e.getKey()), e.getValue());
            }
            JsonObject o = new JsonObject();
            for (Map.Entry<String, Object> e : sorted.entrySet()) o.add(e.getKey(), encode(e.getValue(), depth + 1));
            return o;
        }
        return opaque(v, true);
    }

    private static JsonObject opaque(Object v, boolean withFields) {
        JsonObject o = new JsonObject();
        o.addProperty(
            "class",
            v.getClass()
                .getName());
        if (withFields) {
            TreeMap<String, JsonElement> fields = new TreeMap<>();
            for (Field f : v.getClass()
                .getFields()) {
                if (Modifier.isStatic(f.getModifiers())) continue;
                try {
                    Object fv = f.get(v);
                    if (fv instanceof Number || fv instanceof Boolean || fv instanceof String) {
                        fields.put(f.getName(), simple(fv));
                    } else if (fv instanceof Enum) {
                        fields.put(f.getName(), new JsonPrimitive(((Enum<?>) fv).name()));
                    }
                } catch (Throwable ignored) {
                    // A field we cannot read is left out; "str" still describes the value.
                }
            }
            if (!fields.isEmpty()) {
                JsonObject fo = new JsonObject();
                for (Map.Entry<String, JsonElement> e : fields.entrySet()) fo.add(e.getKey(), e.getValue());
                o.add("fields", fo);
            }
        }
        try {
            o.addProperty("str", String.valueOf(v));
        } catch (Throwable t) {
            o.addProperty("str_error", Json.error(t));
        }
        return o;
    }

    private static JsonElement simple(Object v) {
        if (v instanceof Boolean) return new JsonPrimitive((Boolean) v);
        if (v instanceof String) return new JsonPrimitive((String) v);
        double d = ((Number) v).doubleValue();
        return Double.isNaN(d) || Double.isInfinite(d) ? new JsonPrimitive(v.toString())
            : new JsonPrimitive((Number) v);
    }
}
