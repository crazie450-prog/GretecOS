import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import net.minecraft.block.Block;
import net.minecraft.block.material.Material;
import net.minecraftforge.fluids.Fluid;
import net.minecraftforge.fluids.FluidRegistry;
import net.minecraftforge.fluids.FluidStack;

import org.apache.commons.lang3.tuple.Pair;

import bartworks.system.material.WerkstoffLoader;
import cpw.mods.fml.common.Loader;
import goodgenerator.api.recipe.GoodGeneratorRecipeMaps;
import goodgenerator.items.GGMaterial;
import goodgenerator.main.GGConfigLoader;
import gregtech.api.enums.Materials;
import gregtech.api.enums.Mods;
import gregtech.api.interfaces.tileentity.IGregTechTileEntity;
import gregtech.api.metatileentity.BaseMetaTileEntity;
import gregtech.api.metatileentity.MetaTileEntity;
import gregtech.api.metatileentity.implementations.MTEHatchDynamo;
import gregtech.api.recipe.check.CheckRecipeResult;
import gregtech.api.util.GTRecipeBuilder;
import gregtech.api.util.GTRecipeConstants;
import gregtech.api.util.shutdown.ShutDownReason;
import gregtech.common.tileentities.machines.multi.MTELargeNaquadahReactor;
import gtPlusPlus.xmod.thermalfoundation.fluid.TFFluids;
import tectech.thing.metaTileEntity.hatch.MTEHatchDynamoMulti;
import tectech.thing.metaTileEntity.hatch.MTEHatchDynamoTunnel;

/**
 * Gregtec OS, WO-4.8 (D326): runs GT's own Large Naquadah Reactor (GoodGenerator's, in GT 5.09.54.265:
 * gregtech.common.tileentities.machines.multi.MTELargeNaquadahReactor, mte 15537) on the cases
 * tests/unit/gtmath/oracle/make_naquadah_vectors.py writes, and prints what it computes, so the Python port in
 * core/gregtec/planner/gtmath/naquadah.py can be checked against the real class.
 *
 * Original code; it only calls GT's classes (GT5-Unofficial is LGPL-3.0 and is linked at run time, never copied).
 * Outside the game the oracle sets up only what the reactor touches: Forge's fluid registry (a water and lava
 * block, an FML loader with no mod running), the fluids its coolant and excited-fluid lists name (GT's materials
 * get their molten fluids, GT++'s cryotheum, IC2's coolant, GoodGenerator's atomic separation catalyst), and the
 * fuel recipes of its map, added through GT's own recipe builder with GoodGenerator's LNG_BASIC_OUTPUT metadata,
 * one per (basic output, burn time) pair: GoodGenerator's own nine (GGConfigLoader's arrays, read at run time)
 * and edge cases. Per case the reactor (a subclass that holds the case's tanks and records a machine stop) runs
 * its own checkProcessing_EM and then onRunningTick for every tick of the run, as TTMultiblockBase.onPostTick
 * calls them (progress 0 to the burn time - 1, then the next check at once); its one dynamo hatch is GT's or
 * TecTech's, whose stored EU is read and emptied after each tick. Everything runs on a thread named "Server
 * thread", the server side to GT.
 *
 * Input, one case per line: kind (lists or run), id, then key=value fields. Output: id, then key=value fields, or
 * error=ExceptionClass when GT throws.
 */
public final class GtNaquadahOracle {

    private static sun.misc.Unsafe unsafe;
    private static final List<Fluid> FUELS = new ArrayList<>();
    private static final List<Fluid> DEPLETED = new ArrayList<>();
    private static Fluid liquidAir;
    private static final List<FluidStack> COOLANTS = new ArrayList<>();
    private static final List<FluidStack> EXCITED = new ArrayList<>();

    private GtNaquadahOracle() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            System.err.println("usage: GtNaquadahOracle CASES.tsv RESULTS.tsv");
            System.exit(2);
        }
        final Throwable[] failed = new Throwable[1];
        Thread server = new Thread(() -> {
            try {
                run(args[0], args[1]);
            } catch (Throwable e) {
                failed[0] = e;
            }
        }, "Server thread");
        server.start();
        server.join();
        if (failed[0] != null) {
            failed[0].printStackTrace();
            System.exit(1);
        }
    }

    private static void run(String casesPath, String resultsPath) throws Exception {
        setUp();
        try (BufferedReader in = new BufferedReader(
            new InputStreamReader(Files.newInputStream(Paths.get(casesPath)), StandardCharsets.UTF_8));
            BufferedWriter out = new BufferedWriter(
                new OutputStreamWriter(Files.newOutputStream(Paths.get(resultsPath)), StandardCharsets.UTF_8))) {
            String line;
            int n = 0;
            while ((line = in.readLine()) != null) {
                if (line.isEmpty()) continue;
                String[] cols = line.split("\t");
                Map<String, String> f = new LinkedHashMap<>();
                for (int i = 2; i < cols.length; i++) {
                    int eq = cols[i].indexOf('=');
                    f.put(cols[i].substring(0, eq), cols[i].substring(eq + 1));
                }
                Map<String, String> r = new LinkedHashMap<>();
                try {
                    if ("lists".equals(cols[0])) lists(r);
                    else if ("run".equals(cols[0])) reactor(f, r);
                    else throw new IllegalArgumentException("unknown kind " + cols[0]);
                } catch (RuntimeException e) {
                    r.clear();
                    r.put("error", e.getClass().getSimpleName());
                }
                StringBuilder sb = new StringBuilder(cols[1]);
                for (Map.Entry<String, String> e : r.entrySet()) {
                    sb.append('\t').append(e.getKey()).append('=').append(e.getValue());
                }
                out.write(sb.toString());
                out.write('\n');
                n++;
            }
            System.out.println("GtNaquadahOracle: " + n + " cases");
        }
    }

    // ---- set-up outside the game ----

    private static Field field(Class<?> c, String name) {
        for (Class<?> k = c; k != null; k = k.getSuperclass()) {
            try {
                Field f = k.getDeclaredField(name);
                f.setAccessible(true);
                return f;
            } catch (NoSuchFieldException e) {
                // look in the superclass
            }
        }
        throw new IllegalArgumentException("no field " + name + " on " + c.getName());
    }

    private static void setStatic(Class<?> c, String name, Object v) throws Exception {
        Field f = field(c, name);
        unsafe.putObject(unsafe.staticFieldBase(f), unsafe.staticFieldOffset(f), v);
    }

    private static void setObject(Object o, String name, Object v) {
        Field f = field(o.getClass(), name);
        unsafe.putObject(o, unsafe.objectFieldOffset(f), v);
    }

    private static Fluid fluid(String name) {
        Fluid f = new Fluid(name);
        FluidRegistry.registerFluid(f);
        return FluidRegistry.getFluid(name);
    }

    private static void molten(Materials m, String name) throws Exception {
        Fluid f = fluid(name);
        unsafe.putObject(m, unsafe.objectFieldOffset(field(Materials.class, "mStandardMoltenFluid")), f);
    }

    /** The (basic output, burn time) pairs the fuel map gets: GoodGenerator's nine, then edge cases. */
    static final int[][] EDGES = { { 1, 1 }, { 7, 19 }, { 99, 20 }, { 101, 21 }, { 1000, 39 }, { 3, 41 },
        { Integer.MAX_VALUE, 1000 }, { 123457, 333 }, { 2097152, 20 }, { 2097153, 20 }, { 0, 40 } };

    private static void setUp() throws Exception {
        Field uf = sun.misc.Unsafe.class.getDeclaredField("theUnsafe");
        uf.setAccessible(true);
        unsafe = (sun.misc.Unsafe) uf.get(null);
        Class.forName("net.minecraft.init.Blocks");
        Block water = new Block(Material.water) {};
        water.setBlockName("water");
        setStatic(net.minecraft.init.Blocks.class, "water", water);
        Block lava = new Block(Material.lava) {};
        lava.setBlockName("lava");
        setStatic(net.minecraft.init.Blocks.class, "lava", lava);
        setStatic(Loader.class, "instance", unsafe.allocateInstance(Loader.class));
        // GoodGenerator's config file lives under the game's home: the oracle's working directory stands in, and
        // GGConfigLoader keeps its defaults (its run() reads the file only at the mod's pre-init).
        setStatic(cpw.mods.fml.relauncher.FMLInjectionData.class, "minecraftHome", new java.io.File("."));
        // The coolant and excited fluids the reactor's own lists name.
        molten(Materials.Time, "molten.time");
        molten(Materials.Space, "molten.space");
        molten(Materials.Naquadah, "molten.naquadah");
        molten(Materials.Uranium235, "molten.uranium235");
        molten(Materials.Caesium, "molten.caesium");
        unsafe.putObject(
            Materials.SuperCoolant,
            unsafe.objectFieldOffset(field(Materials.class, "mFluid")),
            fluid("supercoolant"));
        liquidAir = fluid("liquidair");
        unsafe.putObject(Materials.LiquidAir, unsafe.objectFieldOffset(field(Materials.class, "mFluid")), liquidAir);
        setStatic(TFFluids.class, "fluidCryotheum", fluid("cryotheum"));
        fluid("ic2coolant");
        // GTModHandler.getIC2Coolant asks whether IC2 is loaded: it is in the game.
        unsafe.putBoolean(Mods.IndustrialCraft2, unsafe.objectFieldOffset(field(Mods.class, "modLoaded")), true);
        unsafe.putBoolean(Mods.IndustrialCraft2, unsafe.objectFieldOffset(field(Mods.class, "checked")), true);
        // GoodGenerator's materials are bartworks Werkstoffe, which name the mod loading them as their owner.
        cpw.mods.fml.common.ModMetadata meta = new cpw.mods.fml.common.ModMetadata();
        meta.modId = "gregtech";
        meta.name = "GregTech";
        Object controller = unsafe.allocateInstance(cpw.mods.fml.common.LoadController.class);
        setObject(controller, "activeContainer", new cpw.mods.fml.common.DummyModContainer(meta));
        setObject(Loader.instance(), "modController", controller);
        WerkstoffLoader.molten.put(GGMaterial.atomicSeparationCatalyst, fluid("molten.atomicseparationcatalyst"));
        Method coolant = MTELargeNaquadahReactor.class.getDeclaredMethod("getCoolant");
        coolant.setAccessible(true);
        Method excited = MTELargeNaquadahReactor.class.getDeclaredMethod("getExcitedLiquid");
        excited.setAccessible(true);
        for (Object p : (List<?>) coolant.invoke(null)) COOLANTS.add(((Pair<FluidStack, Integer>) p).getKey());
        for (Object p : (List<?>) excited.invoke(null)) EXCITED.add(((Pair<FluidStack, Integer>) p).getKey());
        // GT's recipe builder reads the launcher's blackboard (as GtBoilerOracle sets it up).
        net.minecraft.launchwrapper.Launch.blackboard = new java.util.HashMap<>();
        net.minecraft.launchwrapper.Launch.blackboard.put("fml.deobfuscatedEnvironment", Boolean.TRUE);
        // A recipe asks GT's proxy whether to note who added it (its config flags, all off here).
        setStatic(gregtech.GTMod.class, "proxy", unsafe.allocateInstance(gregtech.common.GTProxy.class));
        // The fuel map: one recipe per pair, through GT's builder as GoodGenerator's FuelRecipeLoader adds its own.
        List<int[]> pairs = new ArrayList<>();
        for (int i = 0; i < GGConfigLoader.NaquadahFuelVoltage.length; i++) {
            pairs.add(new int[] { GGConfigLoader.NaquadahFuelVoltage[i], GGConfigLoader.NaquadahFuelTime[i] });
        }
        for (int[] e : EDGES) pairs.add(e);
        for (int i = 0; i < pairs.size(); i++) {
            Fluid in = fluid("gos.nqfuel." + i);
            Fluid out = fluid("gos.nqdepleted." + i);
            FUELS.add(in);
            DEPLETED.add(out);
            GTRecipeBuilder.builder()
                .fluidInputs(new FluidStack(in, 1))
                .fluidOutputs(new FluidStack(out, 1))
                .duration(pairs.get(i)[1])
                .eut(0)
                .metadata(GTRecipeConstants.LNG_BASIC_OUTPUT, pairs.get(i)[0])
                .addTo(GoodGeneratorRecipeMaps.naquadahReactorFuels);
        }
    }

    // ---- cases ----

    private static String stack(FluidStack s) {
        return s == null ? "none" : s.getFluid().getName() + ":" + s.amount;
    }

    /** GoodGenerator's tunables as this instance has them, and the reactor's own coolant and excited lists. */
    @SuppressWarnings("unchecked")
    private static void lists(Map<String, String> r) throws RuntimeException {
        try {
            r.put("liquid_air_per_second", String.valueOf(GGConfigLoader.LiquidAirConsumptionPerSecond));
            Method coolant = MTELargeNaquadahReactor.class.getDeclaredMethod("getCoolant");
            coolant.setAccessible(true);
            Method excited = MTELargeNaquadahReactor.class.getDeclaredMethod("getExcitedLiquid");
            excited.setAccessible(true);
            int i = 0;
            for (Object o : (List<?>) coolant.invoke(null)) {
                Pair<FluidStack, Integer> p = (Pair<FluidStack, Integer>) o;
                r.put("coolant_" + i++, stack(p.getKey()) + ":" + p.getValue());
            }
            i = 0;
            for (Object o : (List<?>) excited.invoke(null)) {
                Pair<FluidStack, Integer> p = (Pair<FluidStack, Integer>) o;
                r.put("excited_" + i++, stack(p.getKey()) + ":" + p.getValue());
            }
            for (i = 0; i < FUELS.size(); i++) {
                gregtech.api.util.GTRecipe rec = GoodGeneratorRecipeMaps.naquadahReactorFuels.findRecipeQuery()
                    .fluids(new FluidStack(FUELS.get(i), 1))
                    .find();
                r.put(
                    "fuel_" + i,
                    rec == null ? "none" : rec.mSpecialValue + ":" + rec.mDuration + ":" + stack(rec.mFluidInputs[0])
                        .replace(FUELS.get(i).getName(), "in") + ":" + stack(rec.mFluidOutputs[0])
                        .replace(DEPLETED.get(i).getName(), "out"));
            }
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException(e);
        }
    }

    /** The reactor's world: the case's tanks, the stop it asks for. */
    private static final class World {

        final ArrayList<FluidStack> tanks = new ArrayList<>();
        String stopped = "";
    }

    private static IGregTechTileEntity serverSide() {
        return (IGregTechTileEntity) Proxy.newProxyInstance(
            GtNaquadahOracle.class.getClassLoader(),
            new Class<?>[] { IGregTechTileEntity.class },
            (proxy, method, args) -> {
                if ("isServerSide".equals(method.getName())) return true;
                Class<?> t = method.getReturnType();
                if (t == boolean.class) return false;
                if (t == int.class || t == short.class || t == byte.class) return 0;
                if (t == long.class) return 0L;
                if (t == float.class) return 0f;
                if (t == double.class) return 0d;
                return null;
            });
    }

    /** A dynamo hatch's tile: a BaseMetaTileEntity that holds EU and nothing else. */
    private static final class HatchTile extends BaseMetaTileEntity {

        @Override
        public void markDirty() {}
    }

    private static MetaTileEntity hatch(String spec) throws Exception {
        String[] p = spec.split(":");
        Class<?> c = "tt".equals(p[0]) ? MTEHatchDynamoMulti.class
            : "laser".equals(p[0]) ? MTEHatchDynamoTunnel.class : MTEHatchDynamo.class;
        MetaTileEntity h = (MetaTileEntity) unsafe.allocateInstance(c);
        Field tier = field(h.getClass(), "mTier");
        unsafe.putByte(h, unsafe.objectFieldOffset(tier), Byte.parseByte(p[1]));
        if (!"gt".equals(p[0])) {
            unsafe.putInt(h, unsafe.objectFieldOffset(field(h.getClass(), "Amperes")), Integer.parseInt(p[2]));
            unsafe.putInt(h, unsafe.objectFieldOffset(field(h.getClass(), "maxAmperes")), Integer.parseInt(p[2]));
        }
        HatchTile tile = (HatchTile) unsafe.allocateInstance(HatchTile.class);
        setObject(h, "mBaseMetaTileEntity", tile);
        unsafe.putObject(tile, unsafe.objectFieldOffset(field(BaseMetaTileEntity.class, "mMetaTileEntity")), h);
        return h;
    }

    private static long takeEU(MetaTileEntity h) {
        BaseMetaTileEntity tile = (BaseMetaTileEntity) h.getBaseMetaTileEntity();
        Field f = field(BaseMetaTileEntity.class, "mStoredEnergy");
        long eu = unsafe.getLong(tile, unsafe.objectFieldOffset(f));
        unsafe.putLong(tile, unsafe.objectFieldOffset(f), 0L);
        return eu;
    }

    private static long amount(World w, Fluid f) {
        long n = 0;
        for (FluidStack s : w.tanks) if (s.getFluid() == f) n += s.amount;
        return n;
    }

    private static long amount(World w, List<Fluid> fluids) {
        long n = 0;
        for (Fluid f : fluids) n += amount(w, f);
        return n;
    }

    /** index:litres,index:litres...: a tank per entry of the reactor's list named. */
    private static List<Fluid> tanks(World w, String spec, List<FluidStack> list) {
        List<Fluid> out = new ArrayList<>();
        if (spec == null || spec.isEmpty()) return out;
        for (String part : spec.split(",")) {
            String[] c = part.split(":");
            Fluid fl = list.get(Integer.parseInt(c[0])).getFluid();
            w.tanks.add(new FluidStack(fl, Integer.parseInt(c[1])));
            out.add(fl);
        }
        return out;
    }

    /**
     * fuel=index:litres, air=litres, coolant and excited=index:litres[,index:litres] (indices into the reactor's
     * own lists), dynamo=gt:tier or tt:tier:amps (TecTech's multi-amp hatch) or laser:tier:amps, runs=n. Per run: what checkProcessing_EM gave, then the litres of each
     * tank the run used, the depleted fuel it put out, the EU its hatch got, and the last tick's output.
     */
    private static void reactor(Map<String, String> f, Map<String, String> r) throws RuntimeException {
        try {
            World w = new World();
            MTELargeNaquadahReactor lnr = new MTELargeNaquadahReactor("gos.oracle.lnr") {

                @Override
                public ArrayList<FluidStack> getStoredFluids() {
                    return new ArrayList<>(w.tanks);
                }

                @Override
                public void startRecipeProcessing() {}

                @Override
                public void endRecipeProcessing() {}

                @Override
                public void stopMachine(ShutDownReason reason) {
                    w.stopped = reason.getKey();
                }
            };
            setObject(lnr, "mBaseMetaTileEntity", serverSide());
            String[] fuel = f.get("fuel").split(":");
            Fluid fuelFluid = FUELS.get(Integer.parseInt(fuel[0]));
            w.tanks.add(new FluidStack(fuelFluid, Integer.parseInt(fuel[1])));
            if (f.containsKey("air")) w.tanks.add(new FluidStack(liquidAir, Integer.parseInt(f.get("air"))));
            List<Fluid> coolant = tanks(w, f.get("coolant"), COOLANTS);
            List<Fluid> excited = tanks(w, f.get("excited"), EXCITED);
            MetaTileEntity h = hatch(f.get("dynamo"));
            ArrayList<MTEHatchDynamo> gt = new ArrayList<>();
            ArrayList<MTEHatchDynamoMulti> tt = new ArrayList<>();
            if (h instanceof MTEHatchDynamoMulti) tt.add((MTEHatchDynamoMulti) h);
            else gt.add((MTEHatchDynamo) h);
            setObject(lnr, "mDynamoHatches", gt);
            setObject(lnr, "eDynamoMulti", tt);
            r.put("cap", String.valueOf(h.maxEUOutput() * h.maxAmperesOut()));
            int runs = Integer.parseInt(f.get("runs"));
            Fluid depleted = DEPLETED.get(Integer.parseInt(fuel[0]));
            for (int run = 0; run < runs; run++) {
                long fuel0 = amount(w, fuelFluid), air0 = amount(w, liquidAir);
                long cool0 = amount(w, coolant);
                long exc0 = amount(w, excited);
                lnr.mOutputFluids = null;
                lnr.mMaxProgresstime = 0;
                CheckRecipeResult res = lnr.checkProcessing_EM();
                String p = "r" + run + "_";
                r.put(p + "check", res.wasSuccessful() ? "ok" : "fail");
                if (!res.wasSuccessful()) break;
                int duration = lnr.mMaxProgresstime;
                long eu = 0;
                for (int t = 0; t < duration; t++) {
                    lnr.mProgresstime = t;
                    lnr.onRunningTick(null);
                    eu += takeEU(h);
                    if (!w.stopped.isEmpty()) break;
                }
                r.put(p + "duration", String.valueOf(duration));
                r.put(p + "fuel", String.valueOf(fuel0 - amount(w, fuelFluid)));
                r.put(p + "air", String.valueOf(air0 - amount(w, liquidAir)));
                r.put(p + "coolant", String.valueOf(cool0 - amount(w, coolant)));
                r.put(p + "excited", String.valueOf(exc0 - amount(w, excited)));
                FluidStack out = lnr.mOutputFluids == null ? null : lnr.mOutputFluids[0];
                r.put(
                    p + "depleted",
                    out == null ? "none" : (out.getFluid() == depleted ? "" : "other:") + out.amount);
                r.put(p + "eu", String.valueOf(eu));
                r.put(p + "eut", String.valueOf(field(lnr.getClass(), "trueOutput").getLong(lnr)));
                r.put(p + "eff", String.valueOf(field(lnr.getClass(), "trueEff").getInt(lnr)));
                r.put(p + "stopped", w.stopped);
                if (!w.stopped.isEmpty()) break;
            }
        } catch (Exception e) {
            if (e instanceof RuntimeException) throw (RuntimeException) e;
            throw new IllegalStateException(e);
        }
    }
}
