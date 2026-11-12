package io.github.crazie450prog.gregteclink.export;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;

import net.minecraft.item.ItemStack;
import net.minecraftforge.fluids.FluidStack;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import gregtech.api.GregTechAPI;
import gregtech.api.enums.Materials;
import gregtech.api.enums.OrePrefixes;
import gregtech.api.interfaces.IToolStats;
import gregtech.api.interfaces.metatileentity.IMetaTileEntity;
import gregtech.api.metatileentity.implementations.MTEBasicGenerator;
import gregtech.api.recipe.RecipeMap;
import gregtech.api.util.GTOreDictUnificator;
import gregtech.api.util.GTRecipe;
import gregtech.api.util.GTUtility;
import gregtech.api.util.TurbineStatCalculator;
import gregtech.common.items.MetaGeneratedTool01;

/**
 * The power catalog's game data (WO-4.1, D230), what no recipe carries. Turbine rotors: what the planner's turbine
 * port needs. GT's four rotor sizes
 * (the turbine item's tool stats under ids 170, 172, 174, 176) and every material GT makes turbine blades of (the
 * materials its assembler turns blades into rotors for), with the material's tool stats as this instance has them
 * after GT's and the addons' material init. For each material and size it also writes what GT's own
 * TurbineStatCalculator gives for a new rotor, so the port can be checked against the game itself. Floats are written
 * as the double of the float, so every bit survives. Materials are keyed by GT's material name; rotors by the turbine
 * item's registry name and meta (plus the stack's NBT, which names the material).
 * <p>
 * Single-block generators (every {@link MTEBasicGenerator}): GT's fuel efficiency, voltage and amps out, and for each
 * fuel of its recipe map what GT's own generator gives for it ({@code getFuelValue} for one litre of the fluid or one
 * item, and the litres it burns per operation), so the planner's reading of each fuel map can be checked against the
 * game. Fuels are keyed by fluid registry name or item ref, never by numeric IDs.
 */
final class PowerPass {

    static final int[] SIZES = { 170, 172, 174, 176 };
    static final String[] SIZE_NAMES = { "small", "normal", "large", "huge" };

    private final StackJson stacks;
    int materials;
    int generators;
    int errors;

    PowerPass(StackJson stacks) {
        this.stacks = stacks;
    }

    void write(HashedFile out) throws IOException {
        MetaGeneratedTool01 tool = MetaGeneratedTool01.INSTANCE;
        JsonObject root = new JsonObject();
        JsonArray sizes = new JsonArray();
        for (int i = 0; i < SIZES.length; i++) {
            IToolStats s = tool.mToolStats.get((short) SIZES[i]);
            JsonObject o = new JsonObject();
            o.addProperty("size", SIZES[i]);
            o.addProperty("name", SIZE_NAMES[i]);
            if (s != null) {
                o.addProperty("speed_multiplier", (double) s.getSpeedMultiplier());
                o.addProperty("base_damage", (double) s.getBaseDamage());
                o.addProperty("durability_multiplier", (double) s.getMaxDurabilityMultiplier());
            }
            sizes.add(o);
        }
        root.add("sizes", sizes);

        List<Materials> list = new ArrayList<>();
        for (Materials m : Materials.values()) {
            if (m != null && GTOreDictUnificator.get(OrePrefixes.turbineBlade, m, 1L) != null) list.add(m);
        }
        Collections.sort(list, new Comparator<Materials>() {

            @Override
            public int compare(Materials a, Materials b) {
                return a.mName.compareTo(b.mName);
            }
        });
        JsonArray mats = new JsonArray();
        for (Materials m : list) {
            materials++;
            JsonObject o = new JsonObject();
            o.addProperty("name", m.mName);
            o.addProperty("tool_speed", (double) m.mToolSpeed);
            o.addProperty("tool_quality", m.mToolQuality);
            o.addProperty("durability", m.mDurability);
            o.addProperty("steam_multiplier", (double) m.mSteamMultiplier);
            o.addProperty("gas_multiplier", (double) m.mGasMultiplier);
            o.addProperty("plasma_multiplier", (double) m.mPlasmaMultiplier);
            o.add("blade", stacks.item(GTOreDictUnificator.get(OrePrefixes.turbineBlade, m, 1L)));
            JsonArray rotors = new JsonArray();
            for (int size : SIZES) {
                JsonObject r = new JsonObject();
                r.addProperty("size", size);
                try {
                    ItemStack stack = tool.getToolWithStats(size, 1, m, m, null);
                    r.add("rotor", stacks.item(stack));
                    TurbineStatCalculator t = new TurbineStatCalculator(tool, stack);
                    r.addProperty("max_durability", t.getMaxDurability());
                    r.addProperty("base_efficiency", (double) t.getBaseEfficiency());
                    r.addProperty("loose_efficiency", (double) t.getLooseEfficiency());
                    r.addProperty("optimal_flow", (double) t.getOptimalFlow());
                    r.addProperty("optimal_steam_flow", (double) t.getOptimalSteamFlow());
                    r.addProperty("optimal_gas_flow", (double) t.getOptimalGasFlow());
                    r.addProperty("optimal_plasma_flow", (double) t.getOptimalPlasmaFlow());
                    r.addProperty("optimal_loose_steam_flow", (double) t.getOptimalLooseSteamFlow());
                    r.addProperty("optimal_loose_gas_flow", (double) t.getOptimalLooseGasFlow());
                    r.addProperty("optimal_loose_plasma_flow", (double) t.getOptimalLoosePlasmaFlow());
                    r.addProperty("overflow", t.getOverflowEfficiency());
                } catch (Throwable e) {
                    r.addProperty("error", Json.error(e));
                    errors++;
                }
                rotors.add(r);
            }
            o.add("rotors", rotors);
            mats.add(o);
        }
        root.add("materials", mats);
        root.add("generators", generators());
        root.add("containers", containers());
        out.document(root);
    }

    /**
     * The fluid in each fluid container a fuel map lists as its input (GT indexes those fuels by the fluid: a cell of
     * benzene is the fuel benzene), so the catalog can name every fuel by its fluid. Every map whose backend is a fuel
     * map's, sorted by item.
     */
    private JsonArray containers() {
        java.util.TreeMap<String, JsonObject> seen = new java.util.TreeMap<>();
        for (RecipeMap<?> map : RecipeMap.ALL_RECIPE_MAPS.values()) {
            if (!(map.getBackend() instanceof gregtech.api.recipe.maps.FuelBackend)
                && !(map.getBackend() instanceof gregtech.api.recipe.maps.LargeBoilerFuelBackend)) continue;
            for (GTRecipe r : map.getAllRecipes()) {
                if (r.mInputs == null) continue;
                for (ItemStack in : r.mInputs) {
                    if (in == null) continue;
                    ItemStack one = in.copy();
                    one.stackSize = 1;
                    FluidStack inside = GTUtility.getFluidForFilledItem(one, true);
                    if (inside == null) continue;
                    JsonObject o = new JsonObject();
                    o.add("input", stacks.item(one));
                    o.add("fluid", stacks.fluidRef(inside.getFluid()));
                    o.addProperty("mb", inside.amount);
                    seen.put(
                        o.get("input")
                            .toString(),
                        o);
                }
            }
        }
        JsonArray a = new JsonArray();
        for (JsonObject o : seen.values()) a.add(o);
        return a;
    }

    private JsonArray generators() {
        JsonArray a = new JsonArray();
        IMetaTileEntity[] all = GregTechAPI.METATILEENTITIES;
        for (int id = 0; id < all.length; id++) {
            if (!(all[id] instanceof MTEBasicGenerator)) continue;
            MTEBasicGenerator g = (MTEBasicGenerator) all[id];
            generators++;
            JsonObject o = new JsonObject();
            o.addProperty("mte_id", id);
            o.addProperty(
                "class",
                g.getClass()
                    .getName());
            try {
                o.addProperty("tier", g.mTier);
                o.addProperty("efficiency", g.getEfficiency());
                o.addProperty("eu_out", g.maxEUOutput());
                o.addProperty("amps_out", g.maxAmperesOut());
                RecipeMap<?> map = g.getRecipeMap();
                if (map != null) {
                    o.addProperty("recipe_map", map.unlocalizedName);
                    o.add("fuels", fuels(g, map));
                }
            } catch (Throwable t) {
                o.addProperty("error", Json.error(t));
                errors++;
            }
            a.add(o);
        }
        return a;
    }

    /**
     * What the generator gives for each fuel of its map. A fluid fuel: GT's EU per operation for 1 L and the litres an
     * operation burns. An item fuel: GT's EU per item (the int GT's generator uses, and the long before its cap), and
     * for a fluid container also the contained fluid's values, as the generator burns it from its tank.
     */
    private JsonArray fuels(MTEBasicGenerator g, RecipeMap<?> map) {
        List<JsonObject> rows = new ArrayList<>();
        for (GTRecipe r : map.getAllRecipes()) {
            JsonObject f = new JsonObject();
            f.addProperty("special", r.mSpecialValue);
            try {
                FluidStack fluid = null;
                if (r.mFluidInputs != null && r.mFluidInputs.length > 0 && r.mFluidInputs[0] != null) {
                    fluid = new FluidStack(r.mFluidInputs[0], 1);
                } else if (r.mInputs != null && r.mInputs.length > 0 && r.mInputs[0] != null) {
                    ItemStack one = r.mInputs[0].copy();
                    one.stackSize = 1;
                    f.add("input", stacks.item(one));
                    f.addProperty("item_eu", g.getFuelValue(one));
                    f.addProperty("item_eu_long", g.getFuelValue(one, true));
                    FluidStack inside = GTUtility.getFluidForFilledItem(one, true);
                    if (inside != null) fluid = new FluidStack(inside, 1);
                } else {
                    continue;
                }
                if (fluid != null) {
                    f.add("fluid", stacks.fluidRef(fluid.getFluid()));
                    f.addProperty("eu", g.getFuelValue(fluid));
                    f.addProperty("eu_long", g.getFuelValue(fluid, true));
                    f.addProperty("consumed", g.consumedFluidPerOperation(fluid));
                }
            } catch (Throwable t) {
                f.addProperty("error", Json.error(t));
                errors++;
            }
            rows.add(f);
        }
        Collections.sort(rows, new Comparator<JsonObject>() {

            @Override
            public int compare(JsonObject a, JsonObject b) {
                return a.toString()
                    .compareTo(b.toString());
            }
        });
        JsonArray a = new JsonArray();
        for (JsonObject f : rows) a.add(f);
        return a;
    }
}
