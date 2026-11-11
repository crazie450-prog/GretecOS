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

import gregtech.api.recipe.check.CheckRecipeResult;
import gregtech.api.util.GTRecipe;
import gregtech.api.util.OverclockCalculator;
import gregtech.api.util.ParallelHelper;

/**
 * Gregtec OS, WO-3.1 (D174): runs GT's own OverclockCalculator and ParallelHelper on the cases
 * tests/unit/gtmath/oracle/make_vectors.py writes, and prints what they compute, so the Python port in
 * core/gregtec/planner/gtmath can be checked against the real classes.
 *
 * Original code; it only calls GT's public API (GT5-Unofficial is LGPL-3.0 and is linked at run time, never
 * copied). Doubles travel as Java hex strings, so every bit survives.
 *
 * Input, one case per line: kind (oc or par), id, then key=value fields. Output: id, then key=value fields,
 * or error=ExceptionClass when GT throws.
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
     * game (it records the mod that adds it). ParallelHelper reads no other field of it here: it has no outputs,
     * and the inputs are counted by the lambdas below.
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

    private static void parallel(Map<String, String> f, Map<String, String> r) {
        GTRecipe recipe = recipe(i(f, "recipe_eut", 0), i(f, "duration", 0));
        // One input pool: per_craft units per craft, available in the machine (absent: the inputs never limit).
        final boolean limited = f.containsKey("available");
        final long perCraft = l(f, "per_craft", 1);
        final long[] available = { l(f, "available", 0) };
        ParallelHelper h = new ParallelHelper().setRecipe(recipe)
            .setAvailableEUt(l(f, "available_eut", 0))
            .setMaxParallel(i(f, "max_parallel", 1))
            .setEUtModifier(d(f, "par_eut_modifier", 1.0))
            .enableBatchMode(i(f, "batch_size", 1))
            .setConsumption(true)
            .setOutputCalculation(false)
            .setMaxParallelCalculator((rec, max, fluids, items) -> {
                if (!limited) return max;
                if (available[0] <= 0) return 0;
                if (available[0] >= perCraft * (double) max) return max;
                return Math.min(max, (double) available[0] / perCraft);
            })
            .setInputConsumer((rec, n, fluids, items) -> {
                if (n > 0) available[0] -= perCraft * n;
            });
        OverclockCalculator c = b(f, "default_calc") ? null : calculator(f);
        if (c != null) h.setCalculator(c);
        h.build();
        CheckRecipeResult result = h.getResult();
        r.put("check", result.getID());
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
            if (limited) r.put("left", Long.toString(available[0]));
        }
    }
}
