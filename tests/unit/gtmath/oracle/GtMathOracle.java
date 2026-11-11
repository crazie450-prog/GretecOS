import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.stream.Stream;

import gregtech.api.logic.ProcessingLogic;
import gregtech.api.recipe.RecipeMap;
import gregtech.api.recipe.check.CheckRecipeResult;
import gregtech.api.recipe.check.CheckRecipeResultRegistry;
import gregtech.api.util.GTRecipe;
import gregtech.api.util.OverclockCalculator;
import gregtech.api.util.ParallelHelper;

/**
 * Gregtec OS, WO-3.1 (D174): runs GT's own OverclockCalculator, ParallelHelper and ProcessingLogic on the
 * cases tests/unit/gtmath/oracle/make_vectors.py writes, and prints what they compute, so the Python port in
 * core/gregtec/planner/gtmath can be checked against the real classes.
 *
 * Original code; it only calls GT's public API (GT5-Unofficial is LGPL-3.0 and is linked at run time, never
 * copied). Doubles travel as Java hex strings, so every bit survives.
 *
 * Input, one case per line: kind (oc, par or logic), id, then key=value fields. Output: id, then key=value
 * fields, or error=ExceptionClass when GT throws.
 */
public final class GtMathOracle {

    private GtMathOracle() {}

    public static void main(String[] args) throws IOException {
        if (args.length != 2) {
            System.err.println("usage: GtMathOracle CASES.tsv RESULTS.tsv");
            System.exit(2);
        }
        try (BufferedReader in = new BufferedReader(
            new InputStreamReader(Files.newInputStream(Paths.get(args[0])), StandardCharsets.UTF_8));
            BufferedWriter out = new BufferedWriter(
                new OutputStreamWriter(Files.newOutputStream(Paths.get(args[1])), StandardCharsets.UTF_8))) {
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
                    if ("oc".equals(cols[0])) overclock(f, r);
                    else if ("par".equals(cols[0])) parallel(f, r);
                    else if ("logic".equals(cols[0])) logic(f, r);
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
            System.out.println("GtMathOracle: " + n + " cases");
        }
    }

    private static long l(Map<String, String> f, String key, long dflt) {
        String v = f.get(key);
        return v == null ? dflt : Long.parseLong(v);
    }

    private static int i(Map<String, String> f, String key, int dflt) {
        String v = f.get(key);
        return v == null ? dflt : Integer.parseInt(v);
    }

    private static double d(Map<String, String> f, String key, double dflt) {
        String v = f.get(key);
        return v == null ? dflt : Double.parseDouble(v);
    }

    private static boolean b(Map<String, String> f, String key) {
        return "1".equals(f.get(key));
    }

    private static String hex(double x) {
        return Double.toHexString(x);
    }

    /** A calculator set up from the fields, every setter in the order GT's ProcessingLogic calls them. */
    private static OverclockCalculator calculator(Map<String, String> f) {
        OverclockCalculator c = new OverclockCalculator().setRecipeEUt(l(f, "recipe_eut", 0))
            .setAmperage(l(f, "amperage", 1))
            .setEUt(l(f, "voltage", 0))
            .setDuration(i(f, "duration", 0))
            .setDurationModifier(d(f, "duration_modifier", 1.0))
            .setEUtDiscount(d(f, "eut_modifier", 1.0))
            .setAmperageOC(b(f, "amperage_oc"))
            .setParallel(i(f, "parallel", 1))
            .setMaxTierSkips(i(f, "max_tier_skips", 1));
        if (f.containsKey("duration_decrease_per_oc")) {
            c.setDurationDecreasePerOC(d(f, "duration_decrease_per_oc", 2.0));
        }
        if (f.containsKey("eut_increase_per_oc")) c.setEUtIncreasePerOC(d(f, "eut_increase_per_oc", 4.0));
        return machineChanges(c, f);
    }

    /** What machines change on the calculator their processing logic made: heat, laser, caps, no overclock. */
    private static OverclockCalculator machineChanges(OverclockCalculator c, Map<String, String> f) {
        if (f.containsKey("max_overclocks")) c.setMaxOverclocks(i(f, "max_overclocks", 0));
        if (f.containsKey("max_regular_overclocks")) c.setMaxRegularOverclocks(i(f, "max_regular_overclocks", 0));
        c.setLaserOC(b(f, "laser_oc"))
            .setNoOverclock(b(f, "no_overclock"))
            .setHeatOC(b(f, "heat_oc"))
            .setHeatDiscount(b(f, "heat_discount"))
            .setRecipeHeat(i(f, "recipe_heat", 0))
            .setMachineHeat(i(f, "machine_heat", 0));
        if (f.containsKey("heat_discount_exponent")) {
            c.setHeatDiscountMultiplier(d(f, "heat_discount_exponent", 0.95));
        }
        if (f.containsKey("duration_under_one_tick")) {
            final double underOneTick = d(f, "duration_under_one_tick", 0);
            c.setDurationUnderOneTickSupplier(() -> underOneTick);
        }
        return c;
    }

    private static void overclock(Map<String, String> f, Map<String, String> r) {
        OverclockCalculator c = calculator(f);
        r.put("heat_mult", hex(c.calculateHeatDiscountMultiplier()));
        r.put("max_allowed", Long.toString(c.getMaxAllowedRecipeEUt()));
        r.put("mult_before", hex(c.calculateMultiplierUnderOneTick()));
        if (f.containsKey("current_parallel")) c.setCurrentParallel(i(f, "current_parallel", 0));
        r.put("mult_after", hex(c.calculateMultiplierUnderOneTick()));
        c.calculate();
        r.put("eut", Long.toString(c.getConsumption()));
        r.put("duration", Integer.toString(c.getDuration()));
        r.put("overclocks", Integer.toString(c.getPerformedOverclocks()));
        r.put("current_parallel", Integer.toString(c.getCurrentParallel()));
    }

    /**
     * A recipe with only an EU/t and a duration, made without GTRecipe's constructor, which needs a running
     * game (it records the mod that adds it). The helper and the logic read no other field of it here: it has no
     * outputs, and the inputs are counted by the Pool below.
     */
    private static GTRecipe recipe(int eut, int duration) {
        try {
            Field unsafeField = sun.misc.Unsafe.class.getDeclaredField("theUnsafe");
            unsafeField.setAccessible(true);
            sun.misc.Unsafe unsafe = (sun.misc.Unsafe) unsafeField.get(null);
            GTRecipe recipe = (GTRecipe) unsafe.allocateInstance(GTRecipe.class);
            recipe.mEUt = eut;
            recipe.mDuration = duration;
            return recipe;
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException(e);
        }
    }

    private static Object field(Object o, Class<?> owner, String name) {
        try {
            Field fld = owner.getDeclaredField(name);
            fld.setAccessible(true);
            return fld.get(o);
        } catch (ReflectiveOperationException e) {
            return null;
        }
    }

    /** GT's id of a result: a simple result's key ("success", "no_recipe", ...), else the result class's id. */
    private static String checkId(CheckRecipeResult result) {
        Object key = field(result, result.getClass(), "key");
        return key != null ? key.toString() : result.getID();
    }

    /**
     * One input pool: per_craft units per craft, available in the machine (absent: the inputs never limit). It
     * applies GT's rules for one item input (or, with pool_fluid, one fluid input) of
     * GTRecipe.maxParallelCalculatedByInputs and consumeInput, which themselves need a running game.
     */
    private static final class Pool {

        final boolean limited;
        final boolean fluid;
        final long perCraft;
        long available;

        Pool(Map<String, String> f) {
            limited = f.containsKey("available");
            fluid = b(f, "pool_fluid");
            perCraft = l(f, "per_craft", 1);
            available = l(f, "available", 0);
        }

        double maxParallel(int max) {
            if (!limited) return max;
            if (fluid) {
                if (perCraft <= 0) return max;
                if (available <= 0) return 0;
                double current = Math.min((double) max, (double) available / perCraft);
                return current <= 0 ? 0 : current;
            }
            if (available >= perCraft * (double) max) return max;
            if (available <= 0) return 0;
            return Math.min((double) max, (double) available / perCraft);
        }

        void consume(int crafts) {
            if (crafts > 0) available -= perCraft * crafts;
        }

        ParallelHelper on(ParallelHelper h) {
            return h.setMaxParallelCalculator((rec, max, fluids, items) -> maxParallel(max))
                .setInputConsumer((rec, n, fluids, items) -> consume(n));
        }
    }

    private static void parallel(Map<String, String> f, Map<String, String> r) {
        GTRecipe recipe = recipe(i(f, "recipe_eut", 0), i(f, "duration", 0));
        Pool pool = new Pool(f);
        ParallelHelper h = pool.on(
            new ParallelHelper().setRecipe(recipe)
                .setAvailableEUt(l(f, "available_eut", 0))
                .setMaxParallel(i(f, "max_parallel", 1))
                .setEUtModifier(d(f, "par_eut_modifier", 1.0))
                .enableBatchMode(i(f, "batch_size", 1))
                .setConsumption(true)
                .setOutputCalculation(false));
        OverclockCalculator c = b(f, "default_calc") ? null : calculator(f);
        if (c != null) h.setCalculator(c);
        h.build();
        CheckRecipeResult result = h.getResult();
        r.put("check", checkId(result));
        Object required = field(result, result.getClass(), "required");
        if (required != null) r.put("required", required.toString());
        r.put("max_parallel", field(h, ParallelHelper.class, "maxParallel").toString());
        if (result.wasSuccessful()) {
            OverclockCalculator used = (OverclockCalculator) field(h, ParallelHelper.class, "calculator");
            r.put("parallel", Integer.toString(h.getCurrentParallel()));
            r.put("duration_multiplier", hex(h.getDurationMultiplierDouble()));
            r.put("eut", Long.toString(used.getConsumption()));
            r.put("duration", Integer.toString(used.getDuration()));
            r.put("overclocks", Integer.toString(used.getPerformedOverclocks()));
            r.put("current_parallel", Integer.toString(used.getCurrentParallel()));
            if (pool.limited) r.put("left", Long.toString(pool.available));
        }
    }

    /**
     * GT's ProcessingLogic.process() for one recipe its recipe map matched: the logic as a multiblock sets it up
     * (power, parallel, modifiers, batch size, overclock kind), its calculator changed the way machines change
     * it (the Electric Blast Furnace's heat and heat check among them).
     */
    private static void logic(final Map<String, String> f, Map<String, String> r) {
        final GTRecipe recipe = recipe(i(f, "recipe_eut", 0), i(f, "duration", 0));
        recipe.mSpecialValue = i(f, "recipe_heat", 0);
        final Pool pool = new Pool(f);
        ProcessingLogic logic = new ProcessingLogic() {

            @Override
            protected Stream<GTRecipe> findRecipeMatches(RecipeMap<?> map) {
                return Stream.of(recipe);
            }

            @Override
            protected CheckRecipeResult validateRecipe(GTRecipe rec) {
                if (b(f, "check_heat") && rec.mSpecialValue > i(f, "machine_heat", 0)) {
                    return CheckRecipeResultRegistry.insufficientHeat(rec.mSpecialValue);
                }
                return CheckRecipeResultRegistry.SUCCESSFUL;
            }

            @Override
            protected ParallelHelper createParallelHelper(GTRecipe rec) {
                return pool.on(super.createParallelHelper(rec));
            }

            @Override
            protected OverclockCalculator createOverclockCalculator(GTRecipe rec) {
                return machineChanges(super.createOverclockCalculator(rec), f);
            }
        };
        logic.setAvailableVoltage(l(f, "voltage", 0))
            .setAvailableAmperage(l(f, "amperage", 1))
            .setAmperageOC(b(f, "amperage_oc"))
            .setMaxTierSkips(i(f, "max_tier_skips", 1))
            .setMaxParallel(i(f, "max_parallel", 1))
            .setEuModifier(d(f, "eut_modifier", 1.0))
            .setSpeedBonus(d(f, "duration_modifier", 1.0))
            .setBatchSize(i(f, "batch_size", 1))
            .setOverclock(d(f, "duration_decrease_per_oc", 2.0), d(f, "eut_increase_per_oc", 4.0));
        CheckRecipeResult result = logic.process();
        r.put("check", checkId(result));
        Object required = field(result, result.getClass(), "required");
        if (required != null) r.put("required", required.toString());
        r.put("parallel", Integer.toString(logic.getCurrentParallels()));
        if (result.wasSuccessful()) {
            r.put("eut", Long.toString(logic.getCalculatedEut()));
            r.put("duration", Integer.toString(logic.getDuration()));
            if (pool.limited) r.put("left", Long.toString(pool.available));
        }
    }
}
