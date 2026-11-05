package io.github.crazie450prog.gregteclink.export;

import java.io.IOException;
import java.util.Map;
import java.util.TreeMap;
import java.util.TreeSet;

import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraftforge.fluids.Fluid;
import net.minecraftforge.fluids.FluidStack;
import net.minecraftforge.oredict.OreDictionary;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import gregtech.api.objects.ItemData;
import gregtech.api.util.GTOreDictUnificator;

/**
 * Every (item, meta) and fluid that the other files reference, written once each as items.jsonl and fluids.jsonl.
 * Only facts that need the live game are resolved here: names, ore names and GT's unification data.
 */
final class RefTable {

    private final Map<String, Map<Integer, Item>> items = new TreeMap<>();
    private final Map<String, Fluid> fluids = new TreeMap<>();
    int unregistered;
    int itemErrors;

    void item(String name, int meta, Item item) {
        Map<Integer, Item> metas = items.get(name);
        if (metas == null) {
            metas = new TreeMap<>();
            items.put(name, metas);
        }
        if (!metas.containsKey(meta)) metas.put(meta, item);
    }

    void fluid(Fluid fluid) {
        if (!fluids.containsKey(fluid.getName())) fluids.put(fluid.getName(), fluid);
    }

    int itemCount() {
        int n = 0;
        for (Map<Integer, Item> m : items.values()) n += m.size();
        return n;
    }

    int fluidCount() {
        return fluids.size();
    }

    void writeItems(HashedFile out) throws IOException {
        for (Map.Entry<String, Map<Integer, Item>> e : items.entrySet()) {
            for (Map.Entry<Integer, Item> m : e.getValue()
                .entrySet()) {
                out.line(itemLine(e.getKey(), m.getKey(), m.getValue()));
            }
        }
    }

    private JsonObject itemLine(String name, int meta, Item item) {
        JsonObject o = new JsonObject();
        o.addProperty("item", name);
        o.addProperty("meta", meta);
        JsonArray errors = new JsonArray();
        ItemStack stack = new ItemStack(item, 1, meta);
        try {
            o.addProperty("unlocalized", stack.getUnlocalizedName());
        } catch (Throwable t) {
            errors.add(Json.str("unlocalized: " + Json.error(t)));
        }
        try {
            o.addProperty("name", stack.getDisplayName());
        } catch (Throwable t) {
            errors.add(Json.str("name: " + Json.error(t)));
        }
        try {
            TreeSet<String> ores = new TreeSet<>();
            for (int id : OreDictionary.getOreIDs(stack)) ores.add(OreDictionary.getOreName(id));
            o.add("ore", Json.strings(ores));
        } catch (Throwable t) {
            errors.add(Json.str("ore: " + Json.error(t)));
        }
        try {
            ItemData data = GTOreDictUnificator.getAssociation(stack);
            if (data != null) {
                JsonObject gt = new JsonObject();
                gt.addProperty("prefix", data.mPrefix.name());
                gt.addProperty("material", data.mMaterial.mMaterial.mName);
                gt.addProperty("amount", data.mMaterial.mAmount);
                o.add("gt", gt);
            }
        } catch (Throwable t) {
            errors.add(Json.str("gt: " + Json.error(t)));
        }
        if (errors.size() > 0) {
            o.add("errors", errors);
            itemErrors++;
        }
        return o;
    }

    void writeFluids(HashedFile out) throws IOException {
        for (Fluid f : fluids.values()) {
            JsonObject o = new JsonObject();
            o.addProperty("fluid", f.getName());
            try {
                o.addProperty("unlocalized", f.getUnlocalizedName());
                o.addProperty("name", f.getLocalizedName(new FluidStack(f, 1000)));
                o.addProperty("temperature", f.getTemperature());
                o.addProperty("density", f.getDensity());
                o.addProperty("viscosity", f.getViscosity());
                o.addProperty("gaseous", f.isGaseous());
            } catch (Throwable t) {
                o.addProperty("error", Json.error(t));
            }
            out.line(o);
        }
    }
}
