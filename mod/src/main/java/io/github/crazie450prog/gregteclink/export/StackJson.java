package io.github.crazie450prog.gregteclink.export;

import java.util.Map;
import java.util.TreeMap;
import java.util.function.IntFunction;

import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraft.nbt.NBTBase;
import net.minecraft.nbt.NBTTagByte;
import net.minecraft.nbt.NBTTagByteArray;
import net.minecraft.nbt.NBTTagCompound;
import net.minecraft.nbt.NBTTagDouble;
import net.minecraft.nbt.NBTTagFloat;
import net.minecraft.nbt.NBTTagInt;
import net.minecraft.nbt.NBTTagIntArray;
import net.minecraft.nbt.NBTTagList;
import net.minecraft.nbt.NBTTagLong;
import net.minecraft.nbt.NBTTagShort;
import net.minecraft.nbt.NBTTagString;
import net.minecraftforge.fluids.Fluid;
import net.minecraftforge.fluids.FluidStack;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;

import cpw.mods.fml.common.registry.GameData;

/**
 * Item and fluid references, keyed by registry name + meta (+ NBT), never by numeric ID (EndlessIDs). Every reference
 * is also recorded in the {@link RefTable}, which becomes items.jsonl and fluids.jsonl.
 */
public final class StackJson {

    private final RefTable refs;

    public StackJson(RefTable refs) {
        this.refs = refs;
    }

    public static String itemName(Item item) {
        if (item == null) return null;
        Object name = GameData.getItemRegistry()
            .getNameForObject(item);
        return name == null ? null : name.toString();
    }

    /** {"item","meta","n","nbt"?}; an unregistered item gives "item":null plus "class". */
    public JsonElement item(ItemStack stack) {
        if (stack == null) return JsonNull.INSTANCE;
        JsonObject o = new JsonObject();
        Item item = stack.getItem();
        String name = itemName(item);
        int meta = stack.getItemDamage();
        JsonElement tag = stack.getTagCompound() == null ? null : nbt(stack.getTagCompound());
        o.add("item", Json.str(name));
        if (name == null) {
            o.addProperty(
                "class",
                item == null ? "null"
                    : item.getClass()
                        .getName());
            refs.unregistered++;
        } else {
            refs.item(name, meta, item);
            if (tag != null) refs.itemWithNbt(name, stack, Json.COMPACT.toJson(tag));
        }
        o.addProperty("meta", meta);
        o.addProperty("n", stack.stackSize);
        if (tag != null) o.add("nbt", tag);
        return o;
    }

    public JsonArray items(ItemStack[] stacks) {
        JsonArray a = new JsonArray();
        if (stacks != null) {
            for (ItemStack s : stacks) a.add(item(s));
        }
        return a;
    }

    /** {"fluid","mb","nbt"?}; an unregistered fluid gives "fluid":null. */
    public JsonElement fluid(FluidStack stack) {
        if (stack == null) return JsonNull.INSTANCE;
        JsonObject o = new JsonObject();
        Fluid fluid = stack.getFluid();
        o.add("fluid", fluidRef(fluid));
        o.addProperty("mb", stack.amount);
        if (stack.tag != null) {
            o.add("nbt", nbt(stack.tag));
        }
        return o;
    }

    /** The fluid's registry name, recorded in the table; null (and counted) when it has none. */
    public JsonElement fluidRef(Fluid fluid) {
        String name = fluid == null ? null : fluid.getName();
        if (name == null) {
            refs.unregistered++;
        } else {
            refs.fluid(fluid);
        }
        return Json.str(name);
    }

    public JsonArray fluids(FluidStack[] stacks) {
        JsonArray a = new JsonArray();
        if (stacks != null) {
            for (FluidStack s : stacks) a.add(fluid(s));
        }
        return a;
    }

    /** The registry name for a numeric item ID of this game, or null. Only for IDs found inside NBT. */
    static String itemNameById(int id) {
        return itemName(Item.getItemById(id));
    }

    /**
     * Canonical JSON for NBT, in NEI's NBTJson typing so one Python parser reads both sources: compound keys sorted;
     * ints bare; byte, short, long, float and double as strings with a B, S, L, F or D suffix; strings as is; lists and
     * int/byte arrays as arrays; an empty list or array as {"__custom_type": "&lt;tag class&gt;"}.
     *
     * <p>
     * One addition that is not game NBT: a compound shaped like a saved ItemStack (numeric {@code id}, {@code Count},
     * {@code Damage}) also gets {@code "__item"}, the registry name of that ID, because the numeric ID means nothing
     * once the game has exited (EndlessIDs). The NBT itself is unchanged.
     */
    public static JsonElement nbt(NBTBase tag) {
        return nbt(tag, StackJson::itemNameById);
    }

    static JsonElement nbt(NBTBase tag, IntFunction<String> itemNames) {
        if (tag instanceof NBTTagCompound) {
            NBTTagCompound c = (NBTTagCompound) tag;
            TreeMap<String, JsonElement> sorted = new TreeMap<>();
            for (Object k : c.func_150296_c()) sorted.put((String) k, nbt(c.getTag((String) k), itemNames));
            if (isNumber(c.getTag("id")) && isNumber(c.getTag("Count")) && isNumber(c.getTag("Damage"))) {
                sorted
                    .put("__item", Json.str(itemNames.apply(((NBTBase.NBTPrimitive) c.getTag("id")).func_150287_d())));
            }
            JsonObject o = new JsonObject();
            for (Map.Entry<String, JsonElement> e : sorted.entrySet()) o.add(e.getKey(), e.getValue());
            return o;
        }
        if (tag instanceof NBTTagByte) return new JsonPrimitive(((NBTTagByte) tag).func_150290_f() + "B");
        if (tag instanceof NBTTagShort) return new JsonPrimitive(((NBTTagShort) tag).func_150289_e() + "S");
        if (tag instanceof NBTTagInt) return new JsonPrimitive(((NBTTagInt) tag).func_150287_d());
        if (tag instanceof NBTTagLong) return new JsonPrimitive(((NBTTagLong) tag).func_150291_c() + "L");
        if (tag instanceof NBTTagFloat) return new JsonPrimitive(((NBTTagFloat) tag).func_150288_h() + "F");
        if (tag instanceof NBTTagDouble) return new JsonPrimitive(((NBTTagDouble) tag).func_150286_g() + "D");
        if (tag instanceof NBTTagString) return new JsonPrimitive(((NBTTagString) tag).func_150285_a_());
        if (tag instanceof NBTTagList) {
            NBTTagList list = (NBTTagList) tag;
            if (list.tagCount() == 0) return emptyList(tag);
            // NBTTagList has no getter for arbitrary element types: drain a copy.
            NBTTagList copy = (NBTTagList) list.copy();
            JsonArray a = new JsonArray();
            while (copy.tagCount() > 0) a.add(nbt(copy.removeTag(0), itemNames));
            return a;
        }
        if (tag instanceof NBTTagIntArray) {
            int[] v = ((NBTTagIntArray) tag).func_150302_c();
            if (v.length == 0) return emptyList(tag);
            return Json.ints(v);
        }
        if (tag instanceof NBTTagByteArray) {
            byte[] v = ((NBTTagByteArray) tag).func_150292_c();
            if (v.length == 0) return emptyList(tag);
            JsonArray a = new JsonArray();
            for (byte b : v) a.add(new JsonPrimitive(b + "B"));
            return a;
        }
        JsonObject o = new JsonObject();
        o.addProperty(
            "__unsupported",
            tag == null ? "null"
                : tag.getClass()
                    .getName());
        return o;
    }

    private static boolean isNumber(NBTBase tag) {
        return tag instanceof NBTBase.NBTPrimitive;
    }

    private static JsonObject emptyList(NBTBase tag) {
        JsonObject o = new JsonObject();
        o.addProperty(
            "__custom_type",
            tag.getClass()
                .getName());
        return o;
    }
}
