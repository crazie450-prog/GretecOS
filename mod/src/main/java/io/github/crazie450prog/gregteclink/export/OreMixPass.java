package io.github.crazie450prog.gregteclink.export;

import java.io.IOException;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.TreeSet;

import net.minecraft.item.ItemStack;
import net.minecraft.world.WorldProvider;
import net.minecraft.world.WorldServer;
import net.minecraftforge.common.DimensionManager;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;

import gregtech.api.enums.OreMixes;
import gregtech.api.enums.StoneType;
import gregtech.api.interfaces.IOreMaterial;
import gregtech.api.interfaces.IStoneType;
import gregtech.common.WorldgenGTOreLayer;
import gregtech.common.ores.OreInfo;
import gregtech.common.ores.OreManager;
import it.unimi.dsi.fastutil.shorts.ShortShortPair;

/**
 * Ore mixes (plan §5.2 step 6, WO-1.6, D126): GT's worldgen registry, {@link WorldgenGTOreLayer#sList}, the ore layers
 * that really generate (with GT's own height fix-up applied), plus the instance's dimension IDs with the names GT's
 * worldgen matches a layer's dimensions by, so VisualProspecting's veins (by dimension ID and mix name) can be joined
 * to them. Ores are keyed by material name and by the ore block's registry name + meta, never by numeric IDs.
 */
final class OreMixPass {

    private static final String[] LAYERS = { "primary", "secondary", "between", "sporadic" };
    private static final String GC_SPACE_PROVIDER = "micdoodle8.mods.galacticraft.api.prefab.world.gen.WorldProviderSpace";

    private final StackJson stacks;
    int layers;
    int layerErrors;
    int dimensions;

    OreMixPass(StackJson stacks) {
        this.stacks = stacks;
    }

    void write(HashedFile out) throws IOException {
        JsonObject root = new JsonObject();
        root.add("layers", layers());
        root.add("dimensions", dimensions());
        out.document(root);
    }

    private JsonArray layers() {
        List<WorldgenGTOreLayer> list = new ArrayList<>(WorldgenGTOreLayer.sList);
        Collections.sort(list, new Comparator<WorldgenGTOreLayer>() {

            @Override
            public int compare(WorldgenGTOreLayer a, WorldgenGTOreLayer b) {
                return a.mWorldGenName.compareTo(b.mWorldGenName);
            }
        });
        Map<String, OreMixes> builders = new TreeMap<>();
        for (OreMixes mix : OreMixes.VALUES) builders.put(mix.oreMixBuilder.oreMixName, mix);
        JsonArray a = new JsonArray();
        for (WorldgenGTOreLayer l : list) {
            layers++;
            JsonObject o = new JsonObject();
            JsonArray errors = new JsonArray();
            o.addProperty("name", l.mWorldGenName);
            OreMixes mix = builders.get(l.mWorldGenName);
            try {
                o.add("localized", Json.str(mix == null ? null : mix.oreMixBuilder.getLocalizedName()));
            } catch (Throwable t) {
                errors.add(Json.str("localized: " + Json.error(t)));
            }
            // After GT's validateHeights: what worldgen uses, not the builder's numbers.
            o.addProperty("min_y", l.veinHeight.leftShort());
            o.addProperty("max_y", l.veinHeight.rightShort());
            o.addProperty("weight", l.mWeight);
            o.addProperty("density", l.mDensity);
            o.addProperty("size", l.mSize);
            o.add("dims", Json.strings(new TreeSet<>(l.getAllowedDimensions())));
            if (!l.dimVeinHeights.isEmpty()) {
                JsonObject heights = new JsonObject();
                for (Map.Entry<String, ShortShortPair> e : new TreeMap<>(l.dimVeinHeights).entrySet()) {
                    JsonArray pair = new JsonArray();
                    pair.add(
                        new JsonPrimitive(
                            e.getValue()
                                .leftShort()));
                    pair.add(
                        new JsonPrimitive(
                            e.getValue()
                                .rightShort()));
                    heights.add(e.getKey(), pair);
                }
                o.add("dim_heights", heights);
            }
            JsonObject ores = new JsonObject();
            IOreMaterial[] mats = { l.mPrimary, l.mSecondary, l.mBetween, l.mSporadic };
            for (int i = 0; i < LAYERS.length; i++) {
                try {
                    ores.add(LAYERS[i], ore(mats[i]));
                } catch (Throwable t) {
                    errors.add(Json.str(LAYERS[i] + ": " + Json.error(t)));
                }
            }
            o.add("ores", ores);
            if (errors.size() > 0) {
                o.add("errors", errors);
                layerErrors++;
            }
            a.add(o);
        }
        return a;
    }

    /** {material, name, class, stone?, ore?}: the ore block is the variant in plain stone where the ore has one. */
    private JsonObject ore(IOreMaterial m) {
        JsonObject o = new JsonObject();
        if (m == null) {
            o.add("material", Json.str(null));
            return o;
        }
        o.addProperty("material", m.getInternalName());
        o.addProperty("name", m.getLocalizedName());
        o.addProperty(
            "class",
            m.getClass()
                .getName());
        List<IStoneType> stones = m.getValidStones();
        IStoneType stone = stones == null || stones.isEmpty() ? null
            : stones.contains(StoneType.Stone) ? StoneType.Stone : stones.get(0);
        if (stone == null) return o;
        o.addProperty(
            "stone",
            stone instanceof Enum ? ((Enum<?>) stone).name()
                : stone.getClass()
                    .getName());
        ItemStack stack;
        try (OreInfo<IOreMaterial> info = OreInfo.getNewInfo()) {
            info.material = m;
            info.stoneType = stone;
            stack = OreManager.getStack(info, 1);
        }
        if (stack != null) o.add("ore", stacks.item(stack));
        return o;
    }

    /** {dim, name, provider} for every registered dimension, by ID; the name as GT's worldgen matches it. */
    private JsonArray dimensions() {
        Integer[] ids = DimensionManager.getStaticDimensionIDs();
        Arrays.sort(ids);
        JsonArray a = new JsonArray();
        for (Integer id : ids) {
            dimensions++;
            JsonObject o = new JsonObject();
            o.addProperty("dim", id);
            try {
                WorldServer world = DimensionManager.getWorld(id);
                WorldProvider provider = world != null ? world.provider : DimensionManager.createProviderFor(id);
                o.addProperty(
                    "provider",
                    provider.getClass()
                        .getName());
                o.add("name", Json.str(dimensionName(provider)));
            } catch (Throwable t) {
                o.add("error", Json.str(Json.error(t)));
            }
            a.add(o);
        }
        return a;
    }

    /**
     * As GT's {@code DimensionDef.getDimensionName}: a Galacticraft planet or moon is named by its celestial body,
     * anything else by its provider. Reached by reflection, so GregtecLink needs no Galacticraft to build.
     */
    private static String dimensionName(WorldProvider provider) throws ReflectiveOperationException {
        for (Class<?> c = provider.getClass(); c != null; c = c.getSuperclass()) {
            if (!c.getName()
                .equals(GC_SPACE_PROVIDER)) continue;
            Method body = c.getMethod("getCelestialBody");
            Object b = body.invoke(provider);
            if (b == null) break;
            Object name = b.getClass()
                .getMethod("getName")
                .invoke(b);
            if (name != null) return name.toString();
            break;
        }
        return provider.getDimensionName();
    }
}
