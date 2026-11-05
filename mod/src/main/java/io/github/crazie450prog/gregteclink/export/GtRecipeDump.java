package io.github.crazie450prog.gregteclink.export;

import java.io.IOException;
import java.lang.reflect.Field;
import java.util.AbstractMap;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

import net.minecraft.item.ItemStack;
import net.minecraftforge.fluids.FluidStack;
import net.minecraftforge.oredict.OreDictionary;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import cpw.mods.fml.common.ModContainer;
import gregtech.api.recipe.BasicUIProperties;
import gregtech.api.recipe.RecipeCategory;
import gregtech.api.recipe.RecipeMap;
import gregtech.api.recipe.RecipeMetadataKey;
import gregtech.api.util.GTRecipe;

/**
 * (a) of plan §5.2 step 2: every map in {@link RecipeMap#ALL_RECIPE_MAPS} (sorted by name) and every recipe in it
 * (sorted by its JSON line). Raw game facts only; the normaliser (WO-1.3) derives circuits, tiers, signatures and
 * hashes.
 */
final class GtRecipeDump {

    private static final Field METADATA_IDENTIFIER;

    static {
        Field f;
        try {
            // Private, with no getter in GT 5.09.54.265.
            f = RecipeMetadataKey.class.getDeclaredField("identifier");
            f.setAccessible(true);
        } catch (Throwable t) {
            f = null;
        }
        METADATA_IDENTIFIER = f;
    }

    private final StackJson stacks;
    private final MetaJson meta;
    int maps;
    int mapErrors;
    int recipes;
    int recipeErrors;

    GtRecipeDump(StackJson stacks) {
        this.stacks = stacks;
        this.meta = new MetaJson(stacks);
    }

    static List<RecipeMap<?>> sortedMaps() {
        List<RecipeMap<?>> list = new ArrayList<>(RecipeMap.ALL_RECIPE_MAPS.values());
        list.sort(Comparator.comparing(m -> m.unlocalizedName));
        return list;
    }

    /**
     * Each map's recipes are written sorted by their JSON line, so an unchanged pack gives the same file. GT's own
     * order is not stable: recipes are registered in a different order on every run, and the backend keeps its
     * categories in a HashMap keyed by RecipeCategory, which has no hashCode of its own. As "category" is the first
     * key after "map" and "idx", the sort also groups recipes by category. {@code idx} is the position in this order.
     */
    void write(HashedFile mapsFile, HashedFile recipesFile) throws IOException {
        JsonArray mapsJson = new JsonArray();
        for (RecipeMap<?> map : sortedMaps()) {
            maps++;
            mapsJson.add(mapJson(map));
            // Every line has "idx": 0 until it is sorted, so the sort key is the content alone.
            List<Map.Entry<String, JsonObject>> lines = new ArrayList<>();
            for (GTRecipe r : map.getAllRecipes()) {
                JsonObject line;
                try {
                    line = recipeJson(map.unlocalizedName, r);
                } catch (Throwable t) {
                    line = new JsonObject();
                    line.addProperty("map", map.unlocalizedName);
                    line.addProperty("idx", 0);
                    line.addProperty("error", Json.error(t));
                    recipeErrors++;
                }
                lines.add(new AbstractMap.SimpleImmutableEntry<>(Json.COMPACT.toJson(line), line));
            }
            lines.sort(Map.Entry.comparingByKey());
            int idx = 0;
            for (Map.Entry<String, JsonObject> e : lines) {
                // Replaces the 0 in place, so "idx" stays the second key.
                e.getValue()
                    .addProperty("idx", idx++);
                recipesFile.line(e.getValue());
                recipes++;
            }
        }
        mapsFile.document(mapsJson);
    }

    private JsonObject mapJson(RecipeMap<?> map) {
        JsonObject o = new JsonObject();
        JsonArray errors = new JsonArray();
        o.addProperty("name", map.unlocalizedName);
        try {
            o.addProperty("amperage", map.getAmperage());
            o.addProperty(
                "backend",
                map.getBackend()
                    .getClass()
                    .getName());
            o.addProperty(
                "frontend",
                map.getFrontend()
                    .getClass()
                    .getName());
        } catch (Throwable t) {
            errors.add(Json.str("backend: " + Json.error(t)));
        }
        try {
            BasicUIProperties ui = map.getFrontend()
                .getUIProperties();
            JsonObject u = new JsonObject();
            u.addProperty("max_item_in", ui.maxItemInputs);
            u.addProperty("max_item_out", ui.maxItemOutputs);
            u.addProperty("max_fluid_in", ui.maxFluidInputs);
            u.addProperty("max_fluid_out", ui.maxFluidOutputs);
            o.add("ui", u);
        } catch (Throwable t) {
            errors.add(Json.str("ui: " + Json.error(t)));
        }
        try {
            o.addProperty(
                "nei_register",
                map.getFrontend()
                    .getNEIProperties().registerNEI);
        } catch (Throwable t) {
            errors.add(Json.str("nei_register: " + Json.error(t)));
        }
        try {
            RecipeCategory def = map.getDefaultRecipeCategory();
            o.add("owner_mod", Json.str(def == null ? null : modId(def.ownerMod)));
            TreeMap<String, RecipeCategory> cats = new TreeMap<>();
            for (RecipeCategory c : map.getAssociatedCategories()) cats.put(c.unlocalizedName, c);
            JsonArray ca = new JsonArray();
            for (RecipeCategory c : cats.values()) {
                JsonObject co = new JsonObject();
                co.addProperty("name", c.unlocalizedName);
                co.add("owner_mod", Json.str(modId(c.ownerMod)));
                ca.add(co);
            }
            o.add("categories", ca);
        } catch (Throwable t) {
            errors.add(Json.str("categories: " + Json.error(t)));
        }
        try {
            o.addProperty(
                "count",
                map.getAllRecipes()
                    .size());
        } catch (Throwable t) {
            errors.add(Json.str("count: " + Json.error(t)));
        }
        if (errors.size() > 0) {
            o.add("errors", errors);
            mapErrors++;
        }
        return o;
    }

    private JsonObject recipeJson(String mapName, GTRecipe r) {
        JsonObject o = new JsonObject();
        o.addProperty("map", mapName);
        o.addProperty("idx", 0);
        RecipeCategory cat = r.getRecipeCategory();
        o.add("category", Json.str(cat == null ? null : cat.unlocalizedName));
        o.addProperty("eut", r.mEUt);
        o.addProperty("duration", r.mDuration);
        o.addProperty("special", r.mSpecialValue);

        o.add("in", inputs(r));
        if (r.mInputChances != null) o.add("in_chance", Json.ints(r.mInputChances));
        o.add("out", stacks.items(r.mOutputs));
        if (r.mOutputChances != null) o.add("out_chance", Json.ints(r.mOutputChances));
        o.add("fin", fluidInputs(r));
        if (r.mFluidInputChances != null) o.add("fin_chance", Json.ints(r.mFluidInputChances));
        o.add("fout", stacks.fluids(r.mFluidOutputs));
        if (r.mFluidOutputChances != null) o.add("fout_chance", Json.ints(r.mFluidOutputChances));
        if (r.mSpecialItems != null) o.add("special_items", meta.encode(r.mSpecialItems));

        JsonObject md = metadata(r);
        if (md.entrySet()
            .size() > 0) o.add("metadata", md);
        String[] desc = r.getNeiDesc();
        if (desc != null) o.add("nei_desc", Json.strings(desc));

        o.addProperty("enabled", r.mEnabled);
        o.addProperty("hidden", r.mHidden);
        o.addProperty("fake", r.mFakeRecipe);
        o.addProperty("can_be_buffered", r.mCanBeBuffered);
        o.addProperty("needs_empty_output", r.mNeedsEmptyOutput);
        o.addProperty("nbt_sensitive", r.isNBTSensitive);
        if (r.owners != null) {
            List<String> owners = new ArrayList<>();
            for (ModContainer mc : r.owners) owners.add(modId(mc));
            o.add("owners", Json.strings(owners));
        }
        return o;
    }

    /** Positional item inputs; GTRecipe_WithAlt slots also get "ore" (by name) and "alt". */
    private JsonArray inputs(GTRecipe r) {
        JsonArray a = stacks.items(r.mInputs);
        if (!(r instanceof GTRecipe.GTRecipe_WithAlt) || r.mInputs == null) return a;
        GTRecipe.GTRecipe_WithAlt w = (GTRecipe.GTRecipe_WithAlt) r;
        for (int i = 0; i < a.size(); i++) {
            JsonElement e = a.get(i);
            if (!e.isJsonObject()) continue;
            JsonObject slot = e.getAsJsonObject();
            if (w.mOreDictIds != null && i < w.mOreDictIds.length && w.mOreDictIds[i] >= 0) {
                slot.addProperty("ore", OreDictionary.getOreName(w.mOreDictIds[i]));
            }
            if (w.mOreDictAlt != null && i < w.mOreDictAlt.length && w.mOreDictAlt[i] != null) {
                ItemStack[] alts = w.mOreDictAlt[i];
                if (alts.length > 0) slot.add("alt", stacks.items(alts));
            }
        }
        return a;
    }

    private JsonArray fluidInputs(GTRecipe r) {
        JsonArray a = stacks.fluids(r.mFluidInputs);
        if (r.mAltFluidInputs == null) return a;
        for (int i = 0; i < a.size() && i < r.mAltFluidInputs.length; i++) {
            FluidStack[] alts = r.mAltFluidInputs[i];
            JsonElement e = a.get(i);
            if (alts != null && alts.length > 0 && e.isJsonObject()) {
                e.getAsJsonObject()
                    .add("alt", stacks.fluids(alts));
            }
        }
        return a;
    }

    /** Keys are GT's own metadata identifiers (e.g. coil_heat), sorted; one bad value never loses the recipe. */
    private JsonObject metadata(GTRecipe r) {
        TreeMap<String, JsonElement> sorted = new TreeMap<>();
        Collection<Map.Entry<RecipeMetadataKey<?>, Object>> entries = r.getMetadataStorage()
            .getEntries();
        for (Map.Entry<RecipeMetadataKey<?>, Object> e : entries) {
            String key = identifier(e.getKey());
            JsonElement value;
            try {
                value = meta.encode(e.getValue());
            } catch (Throwable t) {
                JsonObject err = new JsonObject();
                err.addProperty("error", Json.error(t));
                value = err;
            }
            sorted.put(key, value);
        }
        JsonObject o = new JsonObject();
        for (Map.Entry<String, JsonElement> e : sorted.entrySet()) o.add(e.getKey(), e.getValue());
        return o;
    }

    private static String identifier(RecipeMetadataKey<?> key) {
        if (METADATA_IDENTIFIER != null) {
            try {
                return (String) METADATA_IDENTIFIER.get(key);
            } catch (Throwable ignored) {
                // Fall through to toString(), which also names the identifier.
            }
        }
        return key.toString();
    }

    static String modId(ModContainer mc) {
        return mc == null ? null : mc.getModId();
    }
}
