import java.io.BufferedWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;

import com.gtnewhorizon.cropsnh.api.ISeedStats;
import com.gtnewhorizon.cropsnh.farming.SeedStats;
import com.gtnewhorizon.cropsnh.tileentity.TileEntityCropSticks;

/**
 * Gregtec OS, WO-6.7 (D273): runs CropsNH 2.0.140's own public static growth and breeding arithmetic outside the
 * game and prints what it computes, so the Python port in core/gregtec/crops/rules.py can be checked against the
 * real class, as WO-3.1's GT oracle does (D174).
 *
 * Original code; it only calls CropsNH's classes (LGPL-3.0, linked at run time from the mod's dev runtime, never
 * copied). Three functions: TileEntityCropSticks.getNutrientsPerCycle over liked biome tags, humidity, sky access,
 * water and fertilizer; getGrowthRate over nutrient points, crop tier and Growth; and variateStat over parent stat
 * lists with a one-entry variation list each (its random draw then has one outcome, so the result is exact). Parent stats are 1..31: SeedStats clamps any other value.
 *
 * Output (one line each), tab-separated:
 *   nutrients  liked  humidity  sky  water  value,value,...   (one value per FERTILIZER entry)
 *   rate       tier   growth    value,value,...                (one value per nutrient point 0..MAX_POINTS)
 *   variate    parents(comma)  value,value,...                 (one value per variation MIN_VAR..MAX_VAR)
 */
public final class CropsNHOracle {

    static final int[] LIKED = { 0, 1, 2, 3 };
    static final String[] HUMIDITY = { "-0.5", "0.0", "0.1", "0.3", "0.4", "0.49", "0.5", "0.51", "0.55", "0.6",
        "0.65", "0.7", "0.75", "0.79", "0.8", "0.85", "0.9", "0.95", "1.0", "2.0" };
    static final int[] STORAGE = { 0, 1, 9, 10, 11, 19, 20, 49, 50, 51, 90, 91, 99, 100, 101, 150, 200 };
    static final int MAX_POINTS = 70;
    static final int MIN_TIER = -1, MAX_TIER = 16;
    static final int MIN_VAR = -35, MAX_VAR = 35;
    static final int[][] PARENTS = { { 1 }, { 31 }, { 1, 2 }, { 1, 1 }, { 30, 31 }, { 31, 31 }, { 5, 6, 7 },
        { 5, 6, 8 }, { 1, 2, 2, 2 }, { 1, 1, 1, 31 }, { 10, 11 }, { 17, 18, 19, 20 }, { 24, 25 }, { 3, 4, 4 } };

    private CropsNHOracle() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            System.err.println("usage: CropsNHOracle RESULTS.tsv");
            System.exit(2);
        }
        try (BufferedWriter out = Files.newBufferedWriter(Paths.get(args[0]), StandardCharsets.UTF_8)) {
            for (int liked : LIKED) {
                for (String humidity : HUMIDITY) {
                    for (boolean sky : new boolean[] { false, true }) {
                        for (int water : STORAGE) {
                            StringBuilder values = new StringBuilder();
                            for (int fertilizer : STORAGE) {
                                if (values.length() > 0) values.append(',');
                                values.append(
                                    TileEntityCropSticks
                                        .getNutrientsPerCycle(liked, Float.parseFloat(humidity), sky, water, fertilizer));
                            }
                            out.write(
                                "nutrients\t" + liked + "\t" + humidity + "\t" + sky + "\t" + water + "\t" + values + "\n");
                        }
                    }
                }
            }
            for (int tier = MIN_TIER; tier <= MAX_TIER; tier++) {
                for (int growth = 0; growth <= 31; growth++) {
                    StringBuilder values = new StringBuilder();
                    for (int points = 0; points <= MAX_POINTS; points++) {
                        if (points > 0) values.append(',');
                        values.append(TileEntityCropSticks.getGrowthRate(points, tier, growth));
                    }
                    out.write("rate\t" + tier + "\t" + growth + "\t" + values + "\n");
                }
            }
            for (int[] parents : PARENTS) {
                List<ISeedStats> stats = new ArrayList<>();
                StringBuilder key = new StringBuilder();
                for (int p : parents) {
                    stats.add(new SeedStats((byte) p, (byte) 0, (byte) 0));
                    if (key.length() > 0) key.append(',');
                    key.append(p);
                }
                StringBuilder values = new StringBuilder();
                for (int v = MIN_VAR; v <= MAX_VAR; v++) {
                    if (v > MIN_VAR) values.append(',');
                    values.append(TileEntityCropSticks.variateStat(new int[] { v }, stats, ISeedStats::getGrowth));
                }
                out.write("variate\t" + key + "\t" + values + "\n");
            }
        }
    }
}
