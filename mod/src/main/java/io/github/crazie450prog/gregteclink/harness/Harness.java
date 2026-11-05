package io.github.crazie450prog.gregteclink.harness;

import java.io.BufferedReader;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.FileVisitResult;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.attribute.BasicFileAttributes;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;

import net.minecraft.launchwrapper.Launch;
import net.minecraft.server.MinecraftServer;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import cpw.mods.fml.common.FMLCommonHandler;
import io.github.crazie450prog.gregteclink.GregtecLink;
import io.github.crazie450prog.gregteclink.export.ExportModule;
import io.github.crazie450prog.gregteclink.export.ExportRun;
import io.github.crazie450prog.gregteclink.export.Json;
import io.github.crazie450prog.gregteclink.export.PathGuard;

/**
 * Headless test harness ({@code ./gradlew runServer -Pharness}). On server start it runs an export in mode "harness",
 * checks the files it wrote, writes {@code <gameDir>/gregteclink/harness-result.json} and shuts the server down; the
 * JVM exits 0 only if every check passed. Active only with {@code -Dgregtec.harness=true} in the deobfuscated dev
 * environment, so it can never fire in a real instance.
 */
public final class Harness {

    public static final String PROPERTY = "gregtec.harness";
    public static final String RESULT = "harness-result.json";

    /** Numeric-ID fields that must never appear (EndlessIDs: key on registry name + meta). NBT is not searched. */
    private static final Set<String> FORBIDDEN_KEYS = new HashSet<>(
        Arrays.asList("id", "item_id", "block_id", "fluid_id", "fluidID", "ore_id", "ore_ids", "oredict_id"));

    private static boolean ran;
    private static int exitCode = 1;

    private Harness() {}

    public static boolean requested() {
        return Boolean.getBoolean(PROPERTY);
    }

    private static boolean deobfEnvironment() {
        return Boolean.TRUE.equals(Launch.blackboard.get("fml.deobfuscatedEnvironment"));
    }

    public static void onServerStarted(File gameDir, boolean exportEnabled) {
        if (!deobfEnvironment()) {
            GregtecLink.LOG.warn("-D{}=true ignored: the harness runs only in the dev environment", PROPERTY);
            if (exportEnabled) ExportModule.onServerStarted();
            return;
        }
        ran = true;
        List<JsonObject> checks = new ArrayList<>();
        String runId = null;
        JsonObject durations = new JsonObject();
        JsonElement counts = null;
        try {
            String world = MinecraftServer.getServer()
                .getFolderName();
            Map<String, String> before = snapshot(gameDir, world);
            check(checks, "export_module_enabled", exportEnabled, "config/gregteclink.cfg modules.export");
            if (exportEnabled) {
                long t0 = System.currentTimeMillis();
                ExportRun.Result r = ExportModule.runForHarness();
                durations.addProperty("export_ms", System.currentTimeMillis() - t0);
                runId = r.runId;
                long t1 = System.currentTimeMillis();
                counts = new Verifier(checks).verify(r);
                Map<String, String> after = snapshot(gameDir, world);
                checkWrites(checks, before, after);
                durations.addProperty("verify_ms", System.currentTimeMillis() - t1);
            }
        } catch (Throwable t) {
            GregtecLink.LOG.error("harness failed", t);
            check(checks, "harness_ran", false, Json.error(t));
        }
        boolean pass = !checks.isEmpty();
        for (JsonObject c : checks) {
            if (!c.get("ok")
                .getAsBoolean()) pass = false;
        }
        JsonObject result = new JsonObject();
        result.addProperty("pass", pass);
        result.add("run_id", Json.str(runId));
        JsonArray ca = new JsonArray();
        for (JsonObject c : checks) ca.add(c);
        result.add("checks", ca);
        result.add("durations_ms", durations);
        if (counts != null) result.add("counts", counts);
        try {
            PathGuard guard = new PathGuard(
                gameDir.toPath()
                    .resolve("gregteclink"));
            Files.write(guard.resolve(RESULT), (Json.PRETTY.toJson(result) + "\n").getBytes(StandardCharsets.UTF_8));
        } catch (Throwable t) {
            GregtecLink.LOG.error("harness: cannot write " + RESULT, t);
            pass = false;
        }
        exitCode = pass ? 0 : 1;
        for (JsonObject c : checks) {
            GregtecLink.LOG.info(
                "harness check {} {}: {}",
                c.get("ok")
                    .getAsBoolean() ? "PASS" : "FAIL",
                c.get("name")
                    .getAsString(),
                c.get("detail")
                    .getAsString());
        }
        GregtecLink.LOG.info("harness {}: shutting down", pass ? "PASSED" : "FAILED");
        MinecraftServer.getServer()
            .initiateShutdown();
    }

    /** The exit code is a second signal; harness-result.json is the contract. */
    public static void onServerStopped() {
        if (ran) {
            FMLCommonHandler.instance()
                .exitJava(exitCode, false);
        }
    }

    static void check(List<JsonObject> checks, String name, boolean ok, String detail) {
        JsonObject c = new JsonObject();
        c.addProperty("name", name);
        c.addProperty("ok", ok);
        c.addProperty("detail", detail == null ? "" : detail);
        checks.add(c);
    }

    /** relpath -> size:mtime for the game dir, without the world, logs, crash reports and config. */
    private static Map<String, String> snapshot(File gameDir, String world) throws IOException {
        final Path root = gameDir.toPath()
            .toAbsolutePath()
            .normalize();
        final Set<String> skip = new HashSet<>(Arrays.asList(world, "logs", "crash-reports", "config"));
        final Map<String, String> out = new TreeMap<>();
        Files.walkFileTree(root, new SimpleFileVisitor<Path>() {

            @Override
            public FileVisitResult preVisitDirectory(Path dir, BasicFileAttributes attrs) {
                if (!dir.equals(root) && dir.getParent()
                    .equals(root)
                    && skip.contains(
                        dir.getFileName()
                            .toString()))
                    return FileVisitResult.SKIP_SUBTREE;
                return FileVisitResult.CONTINUE;
            }

            @Override
            public FileVisitResult visitFile(Path file, BasicFileAttributes attrs) {
                String rel = root.relativize(file)
                    .toString()
                    .replace('\\', '/');
                out.put(rel, attrs.size() + ":" + attrs.lastModifiedTime());
                return FileVisitResult.CONTINUE;
            }

            @Override
            public FileVisitResult visitFileFailed(Path file, IOException exc) {
                return FileVisitResult.CONTINUE;
            }
        });
        return out;
    }

    private static void checkWrites(List<JsonObject> checks, Map<String, String> before, Map<String, String> after) {
        TreeSet<String> outside = new TreeSet<>();
        for (Map.Entry<String, String> e : after.entrySet()) {
            if (e.getValue()
                .equals(before.get(e.getKey()))) continue;
            if (!e.getKey()
                .startsWith("gregteclink/")) outside.add(e.getKey());
        }
        check(
            checks,
            "writes_only_under_gregteclink",
            outside.isEmpty(),
            outside.isEmpty() ? "no file outside gregteclink/ created or changed during the export"
                : "changed: " + outside);
    }

    /** Reads the run back from disk, as the Python side will. */
    private static final class Verifier {

        private final List<JsonObject> checks;
        private final JsonParser parser = new JsonParser();
        private final Set<String> referencedItems = new HashSet<>();
        private final Set<String> referencedFluids = new HashSet<>();
        private int badItemRefs;
        private int forbidden;
        private String forbiddenExample;

        Verifier(List<JsonObject> checks) {
            this.checks = checks;
        }

        JsonElement verify(ExportRun.Result r) throws IOException {
            Path dir = r.dir;
            boolean dirOk = dir != null && Files.isDirectory(dir)
                && !dir.getFileName()
                    .toString()
                    .endsWith(".partial")
                && !Files.exists(dir.resolveSibling(r.runId + ".partial"));
            check(checks, "run_dir_final", dirOk, String.valueOf(dir));
            if (!dirOk) return null;

            JsonObject manifest = parse(dir.resolve("manifest.json")).getAsJsonObject();
            boolean mOk = manifest.get("complete")
                .getAsBoolean()
                && ExportRun.FORMAT.equals(
                    manifest.get("format")
                        .getAsString())
                && "harness".equals(
                    manifest.get("mode")
                        .getAsString());
            check(
                checks,
                "manifest_complete",
                mOk,
                "complete=" + manifest
                    .get("complete") + " format=" + manifest.get("format") + " mode=" + manifest.get("mode"));
            checkFiles(dir, manifest.getAsJsonObject("files"));
            JsonObject counts = manifest.getAsJsonObject("counts");
            JsonObject parts = manifest.getAsJsonObject("parts");

            JsonArray maps = parse(dir.resolve("gt_recipe_maps.json")).getAsJsonArray();
            Map<String, Integer> mapCounts = new TreeMap<>();
            for (JsonElement m : maps) {
                JsonObject o = m.getAsJsonObject();
                mapCounts.put(
                    o.get("name")
                        .getAsString(),
                    o.has("count") ? o.get("count")
                        .getAsInt() : -1);
            }
            check(checks, "recipe_maps_at_least_100", mapCounts.size() >= 100, mapCounts.size() + " maps");
            for (String name : new String[] { "gt.recipe.macerator", "gt.recipe.blastfurnace" }) {
                Integer n = mapCounts.get(name);
                check(checks, "map_" + name, n != null && n > 0, name + " count=" + n);
            }

            RecipeScan scan = new RecipeScan();
            try (BufferedReader in = Files.newBufferedReader(dir.resolve("gt_recipes.jsonl"), StandardCharsets.UTF_8)) {
                String line;
                while ((line = in.readLine()) != null) scan.recipe(
                    parser.parse(line)
                        .getAsJsonObject());
            }
            check(checks, "blastfurnace_coil_heat", scan.coilHeat > 0, scan.coilHeat + " EBF recipes with coil_heat");
            check(checks, "recipe_owners_captured", scan.withOwners > 0, scan.withOwners + " recipes with owners");
            check(checks, "non_consumed_inputs_kept", scan.nZero > 0, scan.nZero + " inputs with n == 0");

            boolean ebf = false;
            try (
                BufferedReader in = Files.newBufferedReader(dir.resolve("gt_machines.jsonl"), StandardCharsets.UTF_8)) {
                String line;
                while ((line = in.readLine()) != null) {
                    JsonObject o = parser.parse(line)
                        .getAsJsonObject();
                    walk(o);
                    if (o.get("class")
                        .getAsString()
                        .endsWith(".MTEElectricBlastFurnace")
                        && o.get("meta")
                            .getAsInt() == 1000
                        && o.has("recipe_maps")
                        && o.get("recipe_maps")
                            .toString()
                            .contains("\"gt.recipe.blastfurnace\"")) {
                        ebf = true;
                    }
                }
            }
            check(checks, "machine_ebf_1000", ebf, "MTEElectricBlastFurnace at gregtech:gt.blockmachines@1000");

            int unregistered = counts.get("unregistered")
                .getAsInt();
            check(
                checks,
                "item_refs_registered",
                badItemRefs == 0 && unregistered == 0,
                "bad refs=" + badItemRefs + " unregistered=" + unregistered);
            check(
                checks,
                "no_numeric_ids",
                forbidden == 0,
                forbidden == 0 ? "none of " + FORBIDDEN_KEYS : forbidden + " found, e.g. " + forbiddenExample);

            Set<String> items = new HashSet<>();
            try (BufferedReader in = Files.newBufferedReader(dir.resolve("items.jsonl"), StandardCharsets.UTF_8)) {
                String line;
                while ((line = in.readLine()) != null) {
                    JsonObject o = parser.parse(line)
                        .getAsJsonObject();
                    items.add(
                        o.get("item")
                            .getAsString() + "@"
                            + o.get("meta")
                                .getAsInt());
                }
            }
            Set<String> fluids = new HashSet<>();
            try (BufferedReader in = Files.newBufferedReader(dir.resolve("fluids.jsonl"), StandardCharsets.UTF_8)) {
                String line;
                while ((line = in.readLine()) != null) {
                    fluids.add(
                        parser.parse(line)
                            .getAsJsonObject()
                            .get("fluid")
                            .getAsString());
                }
            }
            Set<String> missing = new TreeSet<>(referencedItems);
            missing.removeAll(items);
            Set<String> missingFluids = new TreeSet<>(referencedFluids);
            missingFluids.removeAll(fluids);
            check(
                checks,
                "ref_tables_cover_refs",
                missing.isEmpty() && missingFluids.isEmpty(),
                referencedItems.size() + " items, "
                    + referencedFluids.size()
                    + " fluids referenced; missing items="
                    + first(missing)
                    + " fluids="
                    + first(missingFluids));

            int machines = counts.get("machines")
                .getAsInt();
            int machineErrors = counts.get("machine_errors")
                .getAsInt();
            check(
                checks,
                "machine_errors_under_5pct",
                machines > 0 && machineErrors * 20 < machines,
                machineErrors + " of " + machines);
            int recipeErrors = counts.get("recipe_errors")
                .getAsInt();
            check(checks, "recipe_errors_zero", recipeErrors == 0, recipeErrors + " recipe errors");
            String nei = parts.get("nei")
                .getAsString();
            check(
                checks,
                "nei_not_run_on_server",
                "absent".equals(nei) || "skipped_server".equals(nei),
                "parts.nei=" + nei);
            String auto = parts.get("autoexport")
                .getAsString();
            check(checks, "autoexport_off", "off".equals(auto), "parts.autoexport=" + auto);
            return counts;
        }

        private void checkFiles(Path dir, JsonObject files) throws IOException {
            List<String> bad = new ArrayList<>();
            String[] required = { "gt_recipe_maps.json", "gt_recipes.jsonl", "gt_machines.jsonl", "items.jsonl",
                "fluids.jsonl" };
            for (String name : required) {
                if (!files.has(name)) bad.add(name + " not listed");
            }
            for (Map.Entry<String, JsonElement> e : files.entrySet()) {
                Path p = dir.resolve(e.getKey());
                JsonObject want = e.getValue()
                    .getAsJsonObject();
                if (!Files.isRegularFile(p)) {
                    bad.add(e.getKey() + " missing");
                    continue;
                }
                MessageDigest md;
                try {
                    md = MessageDigest.getInstance("SHA-256");
                } catch (Exception ex) {
                    throw new IOException(ex);
                }
                long bytes = 0;
                long lines = 0;
                try (InputStream in = Files.newInputStream(p)) {
                    byte[] buf = new byte[1 << 16];
                    int n;
                    while ((n = in.read(buf)) > 0) {
                        md.update(buf, 0, n);
                        bytes += n;
                        for (int i = 0; i < n; i++) if (buf[i] == '\n') lines++;
                    }
                }
                if (!Json.hex(md.digest())
                    .equals(
                        want.get("sha256")
                            .getAsString())
                    || bytes != want.get("bytes")
                        .getAsLong()
                    || lines != want.get("lines")
                        .getAsLong()) {
                    bad.add(e.getKey() + " sha256/bytes/lines mismatch");
                }
            }
            check(
                checks,
                "files_listed_and_hashed",
                bad.isEmpty(),
                bad.isEmpty() ? files.entrySet()
                    .size() + " files match the manifest" : bad.toString());
        }

        private JsonElement parse(Path p) throws IOException {
            return parser.parse(new String(Files.readAllBytes(p), StandardCharsets.UTF_8));
        }

        /** Item refs need a string "item" (or null plus "class"); no numeric-ID key anywhere outside NBT. */
        private void walk(JsonElement e) {
            if (e.isJsonArray()) {
                for (JsonElement c : e.getAsJsonArray()) walk(c);
            } else if (e.isJsonObject()) {
                JsonObject o = e.getAsJsonObject();
                for (Map.Entry<String, JsonElement> f : o.entrySet()) {
                    String k = f.getKey();
                    if (FORBIDDEN_KEYS.contains(k)) {
                        forbidden++;
                        if (forbiddenExample == null) forbiddenExample = k + " in " + o;
                    }
                    if (k.equals("nbt")) continue;
                    if (k.equals("item")) {
                        JsonElement v = f.getValue();
                        if (v.isJsonPrimitive() && v.getAsJsonPrimitive()
                            .isString()) {
                            if (o.has("meta")) referencedItems.add(
                                v.getAsString() + "@"
                                    + o.get("meta")
                                        .getAsInt());
                        } else if (!(v.isJsonNull() && o.has("class"))) {
                            badItemRefs++;
                        }
                        continue;
                    }
                    if (k.equals("fluid") && f.getValue()
                        .isJsonPrimitive()) {
                        referencedFluids.add(
                            f.getValue()
                                .getAsString());
                        continue;
                    }
                    walk(f.getValue());
                }
            }
        }

        private static String first(Set<String> s) {
            if (s.isEmpty()) return "[]";
            List<String> l = new ArrayList<>(s);
            return (l.size() > 5 ? l.subList(0, 5) : l) + (l.size() > 5 ? " (+" + (l.size() - 5) + ")" : "");
        }

        private final class RecipeScan {

            int coilHeat;
            int withOwners;
            int nZero;

            void recipe(JsonObject o) {
                walk(o);
                if (o.has("error")) return;
                if ("gt.recipe.blastfurnace".equals(
                    o.get("map")
                        .getAsString())
                    && o.has("metadata")
                    && o.getAsJsonObject("metadata")
                        .has("coil_heat"))
                    coilHeat++;
                if (o.has("owners") && o.getAsJsonArray("owners")
                    .size() > 0) withOwners++;
                for (JsonElement in : o.getAsJsonArray("in")) {
                    if (in.isJsonObject() && in.getAsJsonObject()
                        .get("n")
                        .getAsInt() == 0) nZero++;
                }
            }
        }
    }
}
