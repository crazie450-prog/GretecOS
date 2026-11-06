package io.github.crazie450prog.gregteclink.export;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.TreeSet;

import net.minecraft.item.ItemStack;
import net.minecraftforge.oredict.OreDictionary;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

/**
 * The full ore dictionary (D142 (h), D145): one line per oredict name, sorted by name, with every registered member as
 * an item ref (registry name, meta, NBT; wildcard meta 32767 kept raw), never a numeric item or oredict ID. Members
 * go through {@link StackJson}, so each is also listed in items.jsonl.
 */
final class OreDictPass {

    private final StackJson stacks;
    int names;
    int members;
    int errors;

    OreDictPass(StackJson stacks) {
        this.stacks = stacks;
    }

    void write(HashedFile out) throws IOException {
        TreeSet<String> sorted = new TreeSet<>();
        for (String name : OreDictionary.getOreNames()) {
            if (name != null) sorted.add(name);
        }
        for (String name : sorted) {
            names++;
            JsonObject o = new JsonObject();
            o.addProperty("ore", name);
            try {
                o.add("members", members(OreDictionary.getOres(name)));
            } catch (Throwable t) {
                o.addProperty("error", Json.error(t));
                errors++;
            }
            out.line(o);
        }
    }

    private JsonArray members(List<ItemStack> stacks) {
        // One entry per distinct ref, ordered by registry name (unregistered last), meta, then NBT JSON (none first).
        List<Member> found = new ArrayList<>();
        TreeSet<String> seen = new TreeSet<>();
        for (ItemStack s : stacks) {
            if (s == null) continue;
            JsonObject ref = this.stacks.item(s)
                .getAsJsonObject();
            ref.remove("n");
            String key = Json.COMPACT.toJson(ref);
            if (seen.add(key)) found.add(new Member(ref));
        }
        Collections.sort(found);
        JsonArray a = new JsonArray();
        for (Member m : found) a.add(m.ref);
        members += found.size();
        return a;
    }

    private static final class Member implements Comparable<Member> {

        final JsonObject ref;
        final String name;
        final int meta;
        final String nbt;

        Member(JsonObject ref) {
            this.ref = ref;
            JsonElement item = ref.get("item");
            this.name = item.isJsonNull() ? null : item.getAsString();
            this.meta = ref.get("meta")
                .getAsInt();
            JsonElement tag = ref.get("nbt");
            String cls = ref.has("class") ? ref.get("class")
                .getAsString() : "";
            this.nbt = (tag == null ? "" : Json.COMPACT.toJson(tag)) + cls;
        }

        @Override
        public int compareTo(Member o) {
            if (name == null || o.name == null) {
                if (name != o.name) return name == null ? 1 : -1;
            } else {
                int c = name.compareTo(o.name);
                if (c != 0) return c;
            }
            if (meta != o.meta) return meta < o.meta ? -1 : 1;
            return nbt.compareTo(o.nbt);
        }
    }
}
