import java.io.BufferedWriter;
import java.io.OutputStreamWriter;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.EnumMap;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

import net.minecraft.block.Block;
import net.minecraft.block.material.Material;
import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraftforge.fluids.FluidStack;

import cpw.mods.fml.common.IFuelHandler;
import cpw.mods.fml.common.Loader;
import cpw.mods.fml.common.registry.GameRegistry;
import cpw.mods.fml.common.registry.RegistryDelegate;
import gregtech.api.interfaces.IToolStats;
import gregtech.api.items.MetaGeneratedTool;
import gregtech.api.recipe.check.CheckRecipeResultRegistry;
import gregtech.api.recipe.maps.LargeBoilerFuelBackend;
import gregtech.common.items.MetaGeneratedTool01;
import gregtech.common.tileentities.machines.multi.MTELargeBoilerBase;
import gregtech.common.tileentities.machines.multi.MTELargeBoilerBronze;
import gregtech.common.tileentities.machines.multi.MTELargeBoilerSteel;
import gregtech.common.tileentities.machines.multi.MTELargeBoilerTitanium;
import gregtech.common.tileentities.machines.multi.MTELargeBoilerTungstenSteel;
import gregtech.common.tileentities.machines.multi.MTETreeFarm;

/**
 * Gregtec OS, WO-4.2 (D231, D234): runs GT's own 2.9 large boilers' steady-state arithmetic and the Tree Growth
 * Simulator's multipliers, and prints what they compute, so the Python ports in
 * core/gregtec/planner/gtmath/boiler.py and treefarm.py can be checked against the real classes.
 *
 * Original code; it only calls GT's classes through reflection (GT5-Unofficial is LGPL-3.0 and is linked at run
 * time, never copied). A boiler is allocated without its constructor (its structure needs the game's blocks),
 * and gets the final fields its constructor would set from the class's own EUT_GENERATED and SUPERHEATED
 * constants. Then, per programmed circuit: adjustEUtForConfig (the throttled lEUt), adjustBurnTimeForConfig over
 * many fuel loads (its remainder carried), the steam onRunningTick makes from lEUt at full efficiency, and
 * getWaterConsumptionForSteam and getSuperheatedSteam over many ticks; and runtimeBoost over a range of burn
 * times. The tree farm: getTierMultiplier per tier, the mode multipliers and the tool constants.
 *
 * Solid fuels (D380): LargeBoilerFuelBackend.getBurntimeRatioTicks over a range of burn times, and each boiler's
 * own checkProcessing on an item. The boiler is a subclass that only stands in for its hatches (its input bus
 * holds the item, it has no fluid, and the programmed circuit's number is put where setupCircuit puts it); the
 * item's furnace burn value comes from a Forge fuel handler, so GT reads it through GTModHandler.getFuelValue as
 * in the game. Per furnace burn value and circuit the oracle loads SOLID_LOADS items, one after the other, and
 * sums the burn counter GT sets for each (the counter is zeroed in between, as burning it down does), or prints
 * "refused" when GT doesn't take the item; and LargeBoilerFuelBackend.isAllowedFuel for a few items. Two items: one registered as gregtech:gt.blockreinforced, at meta 6
 * (a fuel GT allows in the superheated boilers), and an unregistered one (not allowed there). For this the oracle
 * sets up what GT touches: a water and a lava block and the bucket and bottle items Forge's fluid container
 * registry names, and GregTech as the mod registering the item.
 *
 * Everything runs on a thread named "Server thread" with an empty FML loader, as GtTurbineOracle does.
 *
 * Output (one line each): kind, name, then key=value fields.
 */
public final class GtBoilerOracle {

    static final int[] DURATIONS = { 1, 2, 3, 4, 5, 6, 7, 9, 10, 16, 33, 64, 100, 160, 333, 591, 1000, 1001, 4096,
        12345 };
    static final int LOADS = 1000;
    static final int TICKS = 4800;
    /** Furnace burn values: around 80, each boiler's EUT_GENERATED and the 10 s (16000) the ratio starts at. */
    static final int[] BURN_VALUES = { 79, 80, 159, 479, 480, 500, 1199, 1200, 1600, 1650, 2000, 3210, 4799, 4800,
        12800, 15999, 16000, 16079, 16080, 16081, 19199, 19200, 20000, 32000, 99999, 100000, 160000, 250001, 1000000,
        1500000 };
    static final int[] SOLID_CIRCUITS = { 0, 1, 7, 25 };
    static final String[][] ALLOWED_PROBES = { { "gregtech:gt.blockreinforced", "5" },
        { "gregtech:gt.blockreinforced", "6" }, { "gregtech:gt.blockreinforced", "7" },
        { "gregtech:gt.blockreinforced", "8" }, { "minecraft:coal", "1" }, { "minecraft:coal_block", "0" },
        { "minecraft:blaze_rod", "0" } };
    /** GT carries a burn value's remainder of 80 from item to item: 160 items are two full cycles of it. */
    static final int SOLID_LOADS = 160;

    static final ArrayList<ItemStack> INPUTS = new ArrayList<>();
    static int burnValue;
    static int circuit;

    private static sun.misc.Unsafe unsafe;

    private GtBoilerOracle() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            System.err.println("usage: GtBoilerOracle RESULTS.tsv");
            System.exit(2);
        }
        final Throwable[] failed = new Throwable[1];
        Thread server = new Thread(() -> {
            try {
                run(args[0]);
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

    private static void run(String resultsPath) throws Exception {
        Field uf = sun.misc.Unsafe.class.getDeclaredField("theUnsafe");
        uf.setAccessible(true);
        unsafe = (sun.misc.Unsafe) uf.get(null);
        // An FML loader with no mod running (GT's side check asks it), as GtTurbineOracle sets up.
        Field lf = Loader.class.getDeclaredField("instance");
        unsafe.putObject(unsafe.staticFieldBase(lf), unsafe.staticFieldOffset(lf), unsafe.allocateInstance(Loader.class));
        // The tree farm's static NEI tool list asks GT's tool item for stacks: an item with no tool stats (it
        // gives none) stands in, which the multipliers read here don't touch.
        net.minecraft.launchwrapper.Launch.blackboard = new java.util.HashMap<>();
        net.minecraft.launchwrapper.Launch.blackboard.put("fml.deobfuscatedEnvironment", Boolean.TRUE);
        MetaGeneratedTool01 tool =(MetaGeneratedTool01) unsafe.allocateInstance(MetaGeneratedTool01.class);
        Field stats = field(MetaGeneratedTool.class, "mToolStats");
        unsafe.putObject(tool, unsafe.objectFieldOffset(stats), new ConcurrentHashMap<Short, IToolStats>());
        final Item item = tool;
        unsafe.putObject(tool, unsafe.objectFieldOffset(Item.class.getDeclaredField("delegate")),
            new RegistryDelegate<Item>() {

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
        Field inst = MetaGeneratedTool01.class.getDeclaredField("INSTANCE");
        unsafe.putObject(unsafe.staticFieldBase(inst), unsafe.staticFieldOffset(inst), tool);
        int n = 0;
        try (BufferedWriter out = new BufferedWriter(
            new OutputStreamWriter(Files.newOutputStream(Paths.get(resultsPath)), StandardCharsets.UTF_8))) {
            for (Class<?> c : new Class<?>[] { MTELargeBoilerBronze.class, MTELargeBoilerSteel.class,
                MTELargeBoilerTitanium.class, MTELargeBoilerTungstenSteel.class }) {
                n += boiler(c, out);
            }
            n += treeFarm(out);
            n += solidFuels(out);
        }
        System.out.println("GtBoilerOracle: " + n + " lines");
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

    private static Method method(Class<?> c, String name, Class<?>... types) {
        for (Class<?> k = c; k != null; k = k.getSuperclass()) {
            try {
                Method m = k.getDeclaredMethod(name, types);
                m.setAccessible(true);
                return m;
            } catch (NoSuchMethodException e) {
                // look in the superclass
            }
        }
        throw new IllegalArgumentException("no method " + name + " on " + c.getName());
    }

    private static void setInt(Object o, String name, int v) throws Exception {
        Field f = field(o.getClass(), name);
        unsafe.putInt(o, unsafe.objectFieldOffset(f), v);
    }

    private static void setBool(Object o, String name, boolean v) throws Exception {
        Field f = field(o.getClass(), name);
        unsafe.putBoolean(o, unsafe.objectFieldOffset(f), v);
    }

    private static void line(BufferedWriter out, String kind, String name, String fields) throws Exception {
        out.write(kind + "\t" + name + "\t" + fields + "\n");
    }

    private static int boiler(Class<?> c, BufferedWriter out) throws Exception {
        int eut = c.getField("EUT_GENERATED").getInt(null);
        boolean superheated = c.getField("SUPERHEATED").getBoolean(null);
        MTELargeBoilerBase b = (MTELargeBoilerBase) unsafe.allocateInstance(c);
        setInt(b, "eut", eut);
        setBool(b, "isSuperheated", superheated);
        Method boost = method(c, "runtimeBoost", int.class);
        Method adjustEUt = method(c, "adjustEUtForConfig", int.class);
        Method adjustBurn = method(c, "adjustBurnTimeForConfig", int.class);
        Method water = method(c, "getWaterConsumptionForSteam", long.class);
        Method superSteam = method(c, "getSuperheatedSteam", long.class);
        String name = c.getSimpleName();
        int n = 0;
        StringBuilder sb = new StringBuilder();
        sb.append("eut=").append(b.getEUt()).append("\tsuperheated=").append(b.isSuperheated());
        for (int d : DURATIONS) {
            sb.append("\tboost_").append(d).append('=').append((int) boost.invoke(b, d));
        }
        line(out, "boiler", name, sb.toString());
        n++;
        for (int circuit = 0; circuit <= 25; circuit++) {
            setInt(b, "integratedCircuitConfigFluid", circuit);
            setInt(b, "integratedCircuitConfigSolid", 0);
            int lEUt = (int) adjustEUt.invoke(b, b.getEUt());
            sb = new StringBuilder();
            sb.append("circuit=").append(circuit).append("\tleut=").append(lEUt);
            for (int burn : new int[] { 1, 16, 177, 591 }) {
                setInt(b, "excessProjectedEU", 0);
                long total = 0;
                for (int i = 0; i < LOADS; i++) total += (int) adjustBurn.invoke(b, burn);
                sb.append("\tburn_").append(burn).append('=').append(total);
            }
            // onRunningTick at full efficiency (10000): lEUt x 2 x 10000 / 10000 steam (normal equivalent).
            long steam = (long) lEUt * 2L * 10000L / 10000L;
            setInt(b, "excessWater", 0);
            setInt(b, "excessSuperheatedSteam", 0);
            long w = 0;
            long s = 0;
            for (int t = 0; t < TICKS; t++) {
                w += (int) water.invoke(b, steam);
                s += superheated ? (int) superSteam.invoke(b, steam) : steam;
            }
            sb.append("\tsteam_per_tick=").append(steam).append("\twater_").append(TICKS).append('=').append(w)
                .append("\tout_").append(TICKS).append('=').append(s);
            line(out, "throttle", name, sb.toString());
            n++;
        }
        return n;
    }

    /** GT's boiler on the oracle's input bus: its item, no fluid, and the circuit number a circuit would set. */
    private static void setCircuit(MTELargeBoilerBase b, boolean isFluid) {
        try {
            setInt(b, isFluid ? "integratedCircuitConfigFluid" : "integratedCircuitConfigSolid", circuit);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    static final class Bronze extends MTELargeBoilerBronze {

        Bronze() {
            super("gos.oracle");
        }

        @Override
        public ArrayList<ItemStack> getStoredInputs() {
            return INPUTS;
        }

        @Override
        public ArrayList<FluidStack> getStoredFluids() {
            return new ArrayList<>();
        }

        @Override
        public void updateSlots() {}

        @Override
        protected void setupCircuit(boolean isFluid) {
            setCircuit(this, isFluid);
        }
    }

    static final class Steel extends MTELargeBoilerSteel {

        Steel() {
            super("gos.oracle");
        }

        @Override
        public ArrayList<ItemStack> getStoredInputs() {
            return INPUTS;
        }

        @Override
        public ArrayList<FluidStack> getStoredFluids() {
            return new ArrayList<>();
        }

        @Override
        public void updateSlots() {}

        @Override
        protected void setupCircuit(boolean isFluid) {
            setCircuit(this, isFluid);
        }
    }

    static final class Titanium extends MTELargeBoilerTitanium {

        Titanium() {
            super("gos.oracle");
        }

        @Override
        public ArrayList<ItemStack> getStoredInputs() {
            return INPUTS;
        }

        @Override
        public ArrayList<FluidStack> getStoredFluids() {
            return new ArrayList<>();
        }

        @Override
        public void updateSlots() {}

        @Override
        protected void setupCircuit(boolean isFluid) {
            setCircuit(this, isFluid);
        }
    }

    static final class TungstenSteel extends MTELargeBoilerTungstenSteel {

        TungstenSteel() {
            super("gos.oracle");
        }

        @Override
        public ArrayList<ItemStack> getStoredInputs() {
            return INPUTS;
        }

        @Override
        public ArrayList<FluidStack> getStoredFluids() {
            return new ArrayList<>();
        }

        @Override
        public void updateSlots() {}

        @Override
        protected void setupCircuit(boolean isFluid) {
            setCircuit(this, isFluid);
        }
    }

    private static void setStatic(Class<?> c, String name, Object v) {
        Field f = field(c, name);
        unsafe.putObject(unsafe.staticFieldBase(f), unsafe.staticFieldOffset(f), v);
    }

    private static int solidFuels(BufferedWriter out) throws Exception {
        // Forge's fluid registries, which GT asks whether the item holds a fluid, name these blocks and items.
        Class.forName("net.minecraft.init.Blocks");
        Block water = new Block(Material.water) {};
        water.setBlockName("water");
        setStatic(net.minecraft.init.Blocks.class, "water", water);
        Block lava = new Block(Material.lava) {};
        lava.setBlockName("lava");
        setStatic(net.minecraft.init.Blocks.class, "lava", lava);
        Class.forName("net.minecraft.init.Items");
        for (String name : new String[] { "bucket", "water_bucket", "lava_bucket", "potionitem", "glass_bottle" }) {
            setStatic(net.minecraft.init.Items.class, name, new Item());
        }
        // GregTech registers the allowed fuel, so its registry name is gregtech:gt.blockreinforced.
        cpw.mods.fml.common.ModMetadata meta = new cpw.mods.fml.common.ModMetadata();
        meta.modId = "gregtech";
        meta.name = "GregTech";
        Object controller = unsafe.allocateInstance(cpw.mods.fml.common.LoadController.class);
        unsafe.putObject(
            controller,
            unsafe.objectFieldOffset(field(cpw.mods.fml.common.LoadController.class, "activeContainer")),
            new cpw.mods.fml.common.DummyModContainer(meta));
        unsafe.putObject(
            Loader.instance(),
            unsafe.objectFieldOffset(field(Loader.class, "modController")),
            controller);
        Item allowed = new Item();
        GameRegistry.registerItem(allowed, "gt.blockreinforced");
        Item plain = new Item();
        GameRegistry.registerFuelHandler(new IFuelHandler() {

            @Override
            public int getBurnTime(ItemStack fuel) {
                return burnValue;
            }
        });
        int n = 0;
        StringBuilder sb = new StringBuilder();
        for (int t = 1; t <= 40000; t += t < 400 ? 1 : 37) {
            if (sb.length() > 0) sb.append('\t');
            sb.append("t_")
                .append(t)
                .append('=')
                .append(LargeBoilerFuelBackend.getBurntimeRatioTicks(t, 20));
        }
        line(out, "ratio", "LargeBoilerFuelBackend", sb.toString());
        n++;
        // The solids GT allows in the superheated boilers, by registry name and meta.
        sb = new StringBuilder();
        for (String[] key : ALLOWED_PROBES) {
            if (sb.length() > 0) sb.append('\t');
            sb.append(key[0])
                .append('@')
                .append(key[1])
                .append('=')
                .append(LargeBoilerFuelBackend.isAllowedFuel(key[0], Integer.parseInt(key[1])));
        }
        line(out, "allowed", "LargeBoilerFuelBackend", sb.toString());
        n++;
        for (Class<?> c : new Class<?>[] { Bronze.class, Steel.class, Titanium.class, TungstenSteel.class }) {
            MTELargeBoilerBase b = (MTELargeBoilerBase) unsafe.allocateInstance(c);
            Class<?> gt = c.getSuperclass();
            setInt(b, "eut", gt.getField("EUT_GENERATED").getInt(null));
            setBool(b, "isSuperheated", gt.getField("SUPERHEATED").getBoolean(null));
            for (int cfg : SOLID_CIRCUITS) {
                line(out, "solid", gt.getSimpleName(), solid(b, new ItemStack(allowed, 1, 6), cfg));
                n++;
            }
            line(out, "solid_plain", gt.getSimpleName(), solid(b, new ItemStack(plain, 1, 0), 0));
            n++;
        }
        return n;
    }

    /** checkProcessing on SOLID_LOADS items of each burn value: the burn counters GT set, summed. */
    private static String solid(MTELargeBoilerBase b, ItemStack item, int cfg) throws Exception {
        StringBuilder sb = new StringBuilder();
        sb.append("item=")
            .append(Item.itemRegistry.getNameForObject(item.getItem()))
            .append('@')
            .append(item.getItemDamage())
            .append("\tcircuit=")
            .append(cfg)
            .append("\tloads=")
            .append(SOLID_LOADS);
        circuit = cfg;
        for (int value : BURN_VALUES) {
            burnValue = value;
            item.stackSize = SOLID_LOADS;
            INPUTS.clear();
            INPUTS.add(item);
            for (String f : new String[] { "excessFuel", "excessProjectedEU", "solidBurnTime", "fluidBurnTime" }) {
                setInt(b, f, 0);
            }
            long total = 0;
            boolean refused = false;
            for (int i = 0; i < SOLID_LOADS; i++) {
                if (b.checkProcessing() != CheckRecipeResultRegistry.SUCCESSFUL) {
                    refused = true;
                    break;
                }
                total += b.getSolidBurnTime();
                setInt(b, "solidBurnTime", 0);
            }
            if (refused != (item.stackSize == SOLID_LOADS) || (!refused && item.stackSize != 0)) {
                throw new IllegalStateException("burn value " + value + ": " + item.stackSize + " items left");
            }
            sb.append("\tv_")
                .append(value)
                .append('=')
                .append(refused ? "refused" : Long.toString(total));
        }
        return sb.toString();
    }

    @SuppressWarnings("unchecked")
    private static int treeFarm(BufferedWriter out) throws Exception {
        Method tier = method(MTETreeFarm.class, "getTierMultiplier", int.class);
        StringBuilder sb = new StringBuilder();
        for (int t = 0; t <= 14; t++) {
            if (t > 0) sb.append('\t');
            sb.append("tier_").append(t).append('=').append((int) tier.invoke(null, t));
        }
        Map<?, Integer> modes = (Map<?, Integer>) field(MTETreeFarm.class, "modeMultiplier").get(null);
        for (Map.Entry<?, Integer> e : ((EnumMap<?, Integer>) modes).entrySet()) {
            sb.append("\tmode_").append(((Enum<?>) e.getKey()).name().toLowerCase()).append('=').append(e.getValue());
        }
        for (String f : new String[] { "TICKS_PER_OPERATION", "TOOL_DAMAGE_PER_OPERATION", "TOOL_CHARGE_PER_OPERATION",
            "TOOL_MULTIPLIER_LOGS_SAW", "TOOL_MULTIPLIER_LOGS_BUZZSAW", "TOOL_MULTIPLIER_LOGS_CHAINSAW",
            "TOOL_MULTIPLIER_SAPLINGS_BRANCH_CUTTER", "TOOL_MULTIPLIER_SAPLINGS_GRAFTER", "TOOL_MULTIPLIER_LEAVES_SHEAR",
            "TOOL_MULTIPLIER_LEAVES_WIRE_CUTTER", "TOOL_MULTIPLIER_LEAVES_ELECTRIC_WIRE_CUTTER",
            "TOOL_MULTIPLIER_FRUIT_KNIFE" }) {
            sb.append('\t').append(f.toLowerCase()).append('=').append(field(MTETreeFarm.class, f).getInt(null));
        }
        line(out, "treefarm", "MTETreeFarm", sb.toString());
        return 1;
    }
}
