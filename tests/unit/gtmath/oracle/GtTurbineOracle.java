import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.TreeSet;
import java.util.concurrent.ConcurrentHashMap;

import net.minecraft.block.Block;
import net.minecraft.block.material.Material;
import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraftforge.fluids.Fluid;
import net.minecraftforge.fluids.FluidRegistry;
import net.minecraftforge.fluids.FluidStack;

import cpw.mods.fml.common.Loader;
import cpw.mods.fml.common.registry.RegistryDelegate;
import goodgenerator.blocks.tileEntity.MTESupercriticalFluidTurbineLegacy;
import gregtech.api.enums.Materials;
import gregtech.api.interfaces.IToolStats;
import gregtech.api.items.MetaGeneratedTool;
import gregtech.api.metatileentity.implementations.MTEMultiBlockBase;
import gregtech.api.util.GTModHandler;
import gregtech.api.util.TurbineStatCalculator;
import gregtech.common.items.MetaGeneratedTool01;
import gregtech.common.tileentities.machines.multi.MTELargeTurbineGasLegacy;
import gregtech.common.tileentities.machines.multi.MTELargeTurbineHPSteamLegacy;
import gregtech.common.tileentities.machines.multi.MTELargeTurbinePlasmaLegacy;
import gregtech.common.tileentities.machines.multi.MTELargeTurbineSteamLegacy;
import gregtech.common.tileentities.machines.multi.turbines.MTELargeTurbineGas;
import gregtech.common.tileentities.machines.multi.turbines.MTELargeTurbineHPSteam;
import gregtech.common.tileentities.machines.multi.turbines.MTELargeTurbinePlasma;
import gregtech.common.tileentities.machines.multi.turbines.MTELargeTurbineSCSteam;
import gregtech.common.tileentities.machines.multi.turbines.MTELargeTurbineSteam;
import gregtech.common.tools.ToolTurbineHuge;
import gregtech.common.tools.ToolTurbineLarge;
import gregtech.common.tools.ToolTurbineNormal;
import gregtech.common.tools.ToolTurbineSmall;

/**
 * Gregtec OS, WO-4.1 (D231): runs GT's own TurbineStatCalculator and the large turbines' fluidIntoPower (the
 * 2.9 controllers and their legacy copies) on the cases tests/unit/gtmath/oracle/make_turbine_vectors.py writes,
 * and prints what they compute, so the Python port in core/gregtec/planner/gtmath/turbine.py can be checked
 * against the real classes.
 *
 * Original code; it only calls GT's public API (GT5-Unofficial is LGPL-3.0 and is linked at run time, never
 * copied). Outside the game, Minecraft's registries are not set up, so the oracle sets up only what these
 * classes touch: a water and lava block for Forge's fluid registry, an empty mod loader (no active mod), the
 * fluids the turbines burn (GT's steam lists get their ids, as GT's loader does), and the turbine item with GT's
 * four rotor sizes. A rotor is made by GT's own getToolWithStats and read by GT's own TurbineStatCalculator. A
 * controller gets its fuel value and dynamo cap from the case (in the game, the fuel map and the dynamo hatch),
 * its fitting, and the overflow tier its checkProcessing reads from the rotor; the litres it drains are counted
 * where it drains them. Everything runs on a thread named "Server thread", the server side to GT.
 *
 * Input, one case per line: kind (list, rotor or power), id, then key=value fields. Output: id, then key=value
 * fields, or error=ExceptionClass when GT throws. Floats travel as Java hex strings of their double value.
 */
public final class GtTurbineOracle {

    private static sun.misc.Unsafe unsafe;
    private static MetaGeneratedTool tool;
    private static final Map<String, Fluid> FLUIDS = new LinkedHashMap<>();

    private GtTurbineOracle() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            System.err.println("usage: GtTurbineOracle CASES.tsv RESULTS.tsv");
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
                    if ("list".equals(cols[0])) list(r);
                    else if ("rotor".equals(cols[0])) rotor(f, r);
                    else if ("power".equals(cols[0])) power(f, r);
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
            System.out.println("GtTurbineOracle: " + n + " cases");
        }
    }

    // ---- set-up outside the game ----

    private static void setStatic(Class<?> c, String name, Object v) throws Exception {
        Field f = c.getDeclaredField(name);
        unsafe.putObject(unsafe.staticFieldBase(f), unsafe.staticFieldOffset(f), v);
    }

    private static void setFinal(Object o, Class<?> c, String name, Object v) throws Exception {
        Field f = c.getDeclaredField(name);
        unsafe.putObject(o, unsafe.objectFieldOffset(f), v);
    }

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

    private static void setUp() throws Exception {
        Field uf = sun.misc.Unsafe.class.getDeclaredField("theUnsafe");
        uf.setAccessible(true);
        unsafe = (sun.misc.Unsafe) uf.get(null);
        // Forge's fluid registry names water and lava after their blocks, which a game registers first.
        Class.forName("net.minecraft.init.Blocks");
        Block water = new Block(Material.water) {};
        water.setBlockName("water");
        setStatic(net.minecraft.init.Blocks.class, "water", water);
        Block lava = new Block(Material.lava) {};
        lava.setBlockName("lava");
        setStatic(net.minecraft.init.Blocks.class, "lava", lava);
        // An FML loader with no mod running (the fluid registry asks it for the active mod).
        setStatic(Loader.class, "instance", unsafe.allocateInstance(Loader.class));
        for (String name : new String[] { "steam", "ic2superheatedsteam", "supercriticalsteam", "gos.gasfuel",
            "plasma.gosfuel" }) {
            Fluid fluid = new Fluid(name);
            FluidRegistry.registerFluid(fluid);
            FLUIDS.put(name, fluid);
        }
        GTModHandler.sAnySteamFluidIDs.add(FluidRegistry.getFluidID("steam"));
        GTModHandler.sSuperHeatedSteamFluidIDs.add(FluidRegistry.getFluidID("ic2superheatedsteam"));
        // GT's turbine item, with GT's four rotor sizes under GT's ids, as MetaGeneratedTool01 adds them.
        tool = (MetaGeneratedTool) unsafe.allocateInstance(MetaGeneratedTool01.class);
        ConcurrentHashMap<Short, IToolStats> stats = new ConcurrentHashMap<>();
        stats.put((short) 170, new ToolTurbineSmall());
        stats.put((short) 172, new ToolTurbineNormal());
        stats.put((short) 174, new ToolTurbineLarge());
        stats.put((short) 176, new ToolTurbineHuge());
        setFinal(tool, MetaGeneratedTool.class, "mToolStats", stats);
        final Item item = tool;
        setFinal(tool, Item.class, "delegate", new RegistryDelegate<Item>() {

            public Item get() {
                return item;
            }

            public String name() {
                return "gregtech:gt.metatool.01";
            }

            public Class<Item> type() {
                return Item.class;
            }
        });
    }

    // ---- cases ----

    private static String hex(double d) {
        return Double.toHexString(d);
    }

    /** Every material with tool stats a rotor can be made of (durability and tool speed above 0). */
    private static void list(Map<String, String> r) {
        TreeSet<String> names = new TreeSet<>();
        for (Materials m : Materials.getAll()) {
            if (m != null && m.mDurability > 0 && m.mToolSpeed > 0) names.add(m.mName);
        }
        r.put("names", String.join(",", names));
    }

    private static TurbineStatCalculator calculator(Map<String, String> f) {
        Materials m = Materials.getRealMaterial(f.get("material"));
        if (m == null || m == Materials._NULL) throw new IllegalArgumentException("no material " + f.get("material"));
        ItemStack rotor = tool.getToolWithStats(Integer.parseInt(f.get("size")), 1, m, m, null);
        return new TurbineStatCalculator(tool, rotor);
    }

    private static void rotor(Map<String, String> f, Map<String, String> r) {
        TurbineStatCalculator t = calculator(f);
        Materials m = t.tMaterial;
        r.put("tool_speed", hex(m.mToolSpeed));
        r.put("tool_quality", String.valueOf(m.mToolQuality));
        r.put("durability", String.valueOf(m.mDurability));
        r.put("steam_mult", hex(m.mSteamMultiplier));
        r.put("gas_mult", hex(m.mGasMultiplier));
        r.put("plasma_mult", hex(m.mPlasmaMultiplier));
        r.put("speed_mult", hex(t.tStats.getSpeedMultiplier()));
        r.put("base_damage", hex(t.tStats.getBaseDamage()));
        r.put("dur_mult", hex(t.tStats.getMaxDurabilityMultiplier()));
        r.put("max_durability", String.valueOf(t.getMaxDurability()));
        r.put("base_eff", hex(t.getBaseEfficiency()));
        r.put("loose_eff", hex(t.getLooseEfficiency()));
        r.put("loose_steam_eff", hex(t.getLooseSteamEfficiency()));
        r.put("loose_gas_eff", hex(t.getLooseGasEfficiency()));
        r.put("loose_plasma_eff", hex(t.getLoosePlasmaEfficiency()));
        r.put("opt_flow", hex(t.getOptimalFlow()));
        r.put("opt_steam", hex(t.getOptimalSteamFlow()));
        r.put("opt_gas", hex(t.getOptimalGasFlow()));
        r.put("opt_plasma", hex(t.getOptimalPlasmaFlow()));
        r.put("opt_loose_steam", hex(t.getOptimalLooseSteamFlow()));
        r.put("opt_loose_gas", hex(t.getOptimalLooseGasFlow()));
        r.put("opt_loose_plasma", hex(t.getOptimalLoosePlasmaFlow()));
        r.put("steam_eut", hex(t.getOptimalSteamEUt()));
        r.put("gas_eut", hex(t.getOptimalGasEUt()));
        r.put("plasma_eut", hex(t.getOptimalPlasmaEUt()));
        r.put("loose_steam_eut", hex(t.getOptimalLooseSteamEUt()));
        r.put("loose_gas_eut", hex(t.getOptimalLooseGasEUt()));
        r.put("loose_plasma_eut", hex(t.getOptimalLoosePlasmaEUt()));
        r.put("overflow", String.valueOf(t.getOverflowEfficiency()));
    }

    /** What a controller's hooks into the world return: the case's fuel value and cap; drains are counted. */
    private static final class Hooks {

        int fuelValue;
        long cap;
        Fluid fuel;
        int drained;

        boolean drain(FluidStack stack) {
            if (stack != null && stack.getFluid() == fuel) drained += stack.amount;
            return false;
        }
    }

    private static MTEMultiBlockBase controller(String name, Hooks h) {
        switch (name) {
            case "gas":
                return new MTELargeTurbineGas("gos.oracle.gas") {

                    @Override
                    public int getFuelValue(FluidStack s) {
                        return h.fuelValue;
                    }

                    @Override
                    public long getMaximumOutput() {
                        return h.cap;
                    }

                    @Override
                    public boolean depleteInput(FluidStack s) {
                        return h.drain(s);
                    }
                };
            case "plasma":
                return new MTELargeTurbinePlasma("gos.oracle.plasma") {

                    @Override
                    public int getFuelValue(FluidStack s) {
                        return h.fuelValue;
                    }

                    @Override
                    public long getMaximumOutput() {
                        return h.cap;
                    }

                    @Override
                    public boolean depleteInput(FluidStack s) {
                        return h.drain(s);
                    }
                };
            case "steam":
                return new MTELargeTurbineSteam("gos.oracle.steam") {

                    @Override
                    public long getMaximumOutput() {
                        return h.cap;
                    }

                    @Override
                    public boolean depleteInput(FluidStack s) {
                        return h.drain(s);
                    }
                };
            case "hp_steam":
                return new MTELargeTurbineHPSteam("gos.oracle.hp") {

                    @Override
                    public long getMaximumOutput() {
                        return h.cap;
                    }

                    @Override
                    public boolean depleteInput(FluidStack s) {
                        return h.drain(s);
                    }
                };
            case "sc_steam":
                return new MTELargeTurbineSCSteam("gos.oracle.sc") {

                    @Override
                    public long getMaximumOutput() {
                        return h.cap;
                    }

                    @Override
                    public boolean depleteInput(FluidStack s) {
                        return h.drain(s);
                    }
                };
            case "gas_legacy":
                return new MTELargeTurbineGasLegacy("gos.oracle.gasl") {

                    @Override
                    public int getFuelValue(FluidStack s) {
                        return h.fuelValue;
                    }

                    @Override
                    public long getMaximumOutput() {
                        return h.cap;
                    }

                    @Override
                    public boolean depleteInput(FluidStack s) {
                        return h.drain(s);
                    }
                };
            case "plasma_legacy":
                return new MTELargeTurbinePlasmaLegacy("gos.oracle.plasmal") {

                    @Override
                    public int getFuelValue(FluidStack s) {
                        return h.fuelValue;
                    }

                    @Override
                    public long getMaximumOutput() {
                        return h.cap;
                    }

                    @Override
                    public boolean depleteInput(FluidStack s) {
                        return h.drain(s);
                    }
                };
            case "steam_legacy":
                return new MTELargeTurbineSteamLegacy("gos.oracle.steaml") {

                    @Override
                    public long getMaximumOutput() {
                        return h.cap;
                    }

                    @Override
                    public boolean depleteInput(FluidStack s) {
                        return h.drain(s);
                    }
                };
            case "hp_steam_legacy":
                return new MTELargeTurbineHPSteamLegacy("gos.oracle.hpl") {

                    @Override
                    public long getMaximumOutput() {
                        return h.cap;
                    }

                    @Override
                    public boolean depleteInput(FluidStack s) {
                        return h.drain(s);
                    }
                };
            case "sc_steam_legacy":
                return new MTESupercriticalFluidTurbineLegacy("gos.oracle.scl") {

                    @Override
                    public long getMaximumOutput() {
                        return h.cap;
                    }

                    @Override
                    public boolean depleteInput(FluidStack s) {
                        return h.drain(s);
                    }
                };
            default:
                throw new IllegalArgumentException("unknown turbine " + name);
        }
    }

    private static String fuelFluid(String turbine) {
        if (turbine.startsWith("gas")) return "gos.gasfuel";
        if (turbine.startsWith("plasma")) return "plasma.gosfuel";
        if (turbine.startsWith("hp_steam")) return "ic2superheatedsteam";
        if (turbine.startsWith("sc_steam")) return "supercriticalsteam";
        return "steam";
    }

    private static Method fluidIntoPower(Class<?> c) {
        for (Class<?> k = c; k != null; k = k.getSuperclass()) {
            for (Method m : k.getDeclaredMethods()) {
                if (m.getName().equals("fluidIntoPower") && m.getParameterCount() == 2) {
                    m.setAccessible(true);
                    return m;
                }
            }
        }
        throw new IllegalArgumentException("no fluidIntoPower on " + c.getName());
    }

    private static void power(Map<String, String> f, Map<String, String> r) {
        String turbine = f.get("turbine");
        TurbineStatCalculator t = calculator(f);
        Hooks h = new Hooks();
        h.fuelValue = Integer.parseInt(f.getOrDefault("fuel", "0"));
        h.cap = Long.parseLong(f.get("cap"));
        h.fuel = FLUIDS.get(fuelFluid(turbine));
        MTEMultiBlockBase mte = controller(turbine, h);
        try {
            Class<?> c = mte.getClass();
            field(c, "looseFit").setBoolean(mte, "1".equals(f.get("loose")));
            // checkProcessing reads these from the rotor before it calls fluidIntoPower.
            field(c, "overflowMultiplier").setInt(mte, t.getOverflowEfficiency());
            Field maxPower = null;
            try {
                maxPower = field(c, "maxPower");
            } catch (IllegalArgumentException e) {
                // the legacy GT turbines have no maxPower: they call getMaximumOutput
            }
            if (maxPower != null) maxPower.setLong(mte, h.cap);
            try {
                field(c, "achievement").setBoolean(mte, true); // no player to award it to
            } catch (IllegalArgumentException e) {
                // not a steam turbine
            }
            ArrayList<FluidStack> fluids = new ArrayList<>();
            int available = Integer.parseInt(f.get("available"));
            if (available > 0) fluids.add(new FluidStack(h.fuel, available));
            int eut = (Integer) fluidIntoPower(c).invoke(mte, fluids, t);
            r.put("eut", String.valueOf(eut));
            r.put("flow", String.valueOf(h.drained));
            r.put("opt", hex(field(c, "realOptFlow").getDouble(mte)));
        } catch (ReflectiveOperationException e) {
            Throwable cause = e.getCause() != null ? e.getCause() : e;
            if (cause instanceof RuntimeException) throw (RuntimeException) cause;
            throw new IllegalStateException(cause);
        }
    }
}
