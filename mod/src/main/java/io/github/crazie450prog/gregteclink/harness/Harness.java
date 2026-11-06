package io.github.crazie450prog.gregteclink.harness;

import java.io.BufferedReader;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.Charset;
import java.nio.charset.StandardCharsets;
import java.nio.file.DirectoryStream;
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
import java.util.function.Consumer;

import net.minecraft.launchwrapper.Launch;
import net.minecraft.server.MinecraftServer;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import cpw.mods.fml.common.FMLCommonHandler;
import io.github.crazie450prog.gregteclink.GregtecLink;
import io.github.crazie450prog.gregteclink.export.AutoExport;
import io.github.crazie450prog.gregteclink.export.ExportClient;
import io.github.crazie450prog.gregteclink.export.ExportModule;
import io.github.crazie450prog.gregteclink.export.ExportRun;
import io.github.crazie450prog.gregteclink.export.Json;
import io.github.crazie450prog.gregteclink.export.PathGuard;

/**
 * Headless test harness ({@code ./gradlew runServer -Pharness}). On server start it runs an export in mode "harness",
 * checks the files it wrote, writes {@code <gameDir>/gregteclink/harness-result.json} and shuts the server down; the
 * JVM exits 0 only if every check passed. On a client ({@code ./gradlew runClient25 -Pharness}, under Xvfb in CI) the
 * run is handed to {@link ExportClient}, which opens the autoexport world, runs NEI's dump and calls
 * {@link #onClientFinished}, leaves the world and calls {@link #onClientShutdown}; the result file is then the only
 * signal. Active only with {@code -Dgregtec.harness=true} in the deobfuscated dev environment, so it can never fire in
 * a real instance.
 */
public final class Harness {

    public static final String PROPERTY = "gregtec.harness";
    public static final String RESULT = "harness-result.json";
    /** Set by {@code -PharnessNeiFail}: the client harness expects NEI's dump to fail. */
    private static final String NEI_FAIL_PROPERTY = "gregtec.harness.neiFail";

    /** Numeric-ID fields that must never appear (EndlessIDs: key on registry name + meta). NBT is not searched. */
    private static final Set<String> FORBIDDEN_KEYS = new HashSet<>(
        Arrays.asList("id", "item_id", "block_id", "fluid_id", "fluidID", "ore_id", "ore_ids", "oredict_id"));

    /** Complete runs seeded before the export: with the harness run, two more than pruning keeps (D98). */
    private static final int SEEDED_COMPLETE = 11;

    /** Where the export may write: the server harness's game dir (the world, logs and crash reports aside). */
    private static final List<String> SERVER_WRITES = Arrays.asList("gregteclink/");
    /**
     * Where the NEI step may write in the client harness's game dir, {@code saves/} aside (the world, and NEI's
     * per-world data; the decoy world is checked on its own): GregtecLink's output, and NEI's dump, which GregtecLink
     * copies and never changes. The client harness checks only the two windows in which the export's code runs (the
     * GT parts at server start; the NEI step to the end of the run): joining a world makes the game and other mods
     * write files of their own (Forge's usernamecache.json, ServerUtilities, NEI's, GT's and AE2's configs), as it does
     * for a world opened by hand.
     */
    private static final List<String> CLIENT_WRITES = Arrays.asList("gregteclink/", "dumps/recipes/");
    /** The crop step's folder in a run, and the CropsNH dumper files Python reads (WO-1.10). */
    private static final String CROPS_DIR = "crops/";
    private static final List<String> CROP_FILES = Arrays.asList(
        "alternateSeed.csv",
        "cropRegistry.csv",
        "deterministicMutations.csv",
        "mutationPools.csv",
        "soilRegistry.txt");
    /** Loose: the dev runtime registers only the crops of the mods it has, not the pack's. */
    private static final int MIN_CROPS = 50;
    /** NEI handler ids of CropsNH's tabs start with its mod id ({@code cropsnhBreeding}, {@code cropsnhCrops}). */
    private static final String CROP_HANDLER_PREFIX = "cropsnh";
    /** Seeded by prepareClientHarness: a file in the old autoexport world, and a marked world with another name. */
    private static final String STALE_FILE = "harness-stale.txt";
    private static final String DECOY_WORLD = "gregteclink-harness-decoy";
    private static final String DECOY_FILE = "decoy.txt";

    private static boolean ran;
    private static int exitCode = 1;
    private static volatile boolean clientActive;
    private static List<JsonObject> clientChecks;
    /** The client game dir just before the NEI step; null until then. */
    private static Map<String, String> neiBefore;
    private static long clientT0;
    /** Set by {@link #onClientFinished}, written by {@link #onClientShutdown}. */
    private static String clientRunId;
    private static JsonObject clientDurations;
    private static JsonElement clientCounts;
    private static long clientFinishedMs;

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
        if (FMLCommonHandler.instance()
            .getSide()
            .isClient()) {
            startClient(gameDir, exportEnabled);
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
                seedOldRuns(gameDir);
                long t0 = System.currentTimeMillis();
                ExportRun.Result r = ExportModule.runForHarness();
                durations.addProperty("export_ms", System.currentTimeMillis() - t0);
                runId = r.runId;
                checkPrune(checks, r);
                long t1 = System.currentTimeMillis();
                counts = new Verifier(checks, gameDir, false).verify(r);
                Map<String, String> after = snapshot(gameDir, world);
                checkWrites(checks, "writes_only_under_gregteclink", before, after, SERVER_WRITES);
                durations.addProperty("verify_ms", System.currentTimeMillis() - t1);
            }
        } catch (Throwable t) {
            GregtecLink.LOG.error("harness failed", t);
            check(checks, "harness_ran", false, Json.error(t));
        }
        exitCode = writeResult(gameDir, checks, runId, durations, counts) ? 0 : 1;
        GregtecLink.LOG.info("harness {}: shutting down", exitCode == 0 ? "PASSED" : "FAILED");
        MinecraftServer.getServer()
            .initiateShutdown();
    }

    /**
     * Client harness ({@code ./gradlew runClient25 -Pharness}, with autoexport): at server start, seeds old runs,
     * starts a harness-mode run and hands it to the client like any other. {@link ExportClient} then runs NEI's dump,
     * finishes the run, calls {@link #onClientFinished}, leaves the world and calls {@link #onClientShutdown}.
     */
    private static void startClient(File gameDir, boolean exportEnabled) {
        List<JsonObject> checks = new ArrayList<>();
        check(checks, "export_module_enabled", exportEnabled, "config/gregteclink.cfg modules.export");
        check(checks, "autoexport_requested", AutoExport.requested(), "-D" + AutoExport.PROPERTY);
        try {
            if (!exportEnabled || !AutoExport.requested()) throw new IllegalStateException("nothing would finish");
            Map<String, String> before = snapshot(gameDir, "saves");
            seedOldRuns(gameDir);
            clientChecks = checks;
            clientT0 = System.currentTimeMillis();
            clientActive = true;
            ExportModule.startForHarness(); // the GT parts, on this (server) thread
            checkWrites(checks, "writes_only_under_gregteclink", before, snapshot(gameDir, "saves"), SERVER_WRITES);
        } catch (Throwable t) {
            GregtecLink.LOG.error("client harness failed", t);
            check(checks, "harness_ran", false, Json.error(t));
            writeResult(gameDir, checks, null, new JsonObject(), null);
            FMLCommonHandler.instance()
                .exitJava(1, false);
        }
    }

    /** True from the client harness's server start until {@link #onClientShutdown}. */
    public static boolean clientRunActive() {
        return clientActive;
    }

    /** Called by {@link ExportClient} on the client thread just before NEI's dump. */
    public static void beforeNeiStep(File gameDir) {
        try {
            neiBefore = snapshot(gameDir, "saves");
        } catch (Throwable t) {
            GregtecLink.LOG.error("client harness: no snapshot before the NEI step", t);
        }
    }

    /**
     * Called by {@link ExportClient} on the client thread, still in the world, when it starts to quit; {@code r} is
     * null if no run finished. Checks the run; {@link #onClientShutdown} writes the result.
     */
    public static void onClientFinished(File gameDir, ExportRun.Result r) {
        clientFinishedMs = System.currentTimeMillis();
        clientRunId = r == null ? null : r.runId;
        List<JsonObject> checks = clientChecks;
        JsonObject durations = new JsonObject();
        JsonElement counts = null;
        try {
            durations.addProperty("export_ms", System.currentTimeMillis() - clientT0);
            Map<String, String> after = snapshot(gameDir, "saves");
            check(checks, "run_finished", r != null, r == null ? "no run finished" : r.runId);
            if (neiBefore == null) {
                check(checks, "nei_step_writes_only_gregteclink_and_dumps", false, "the NEI step never ran");
            } else {
                checkWrites(checks, "nei_step_writes_only_gregteclink_and_dumps", neiBefore, after, clientWrites(r));
            }
            if (r != null) {
                checkPrune(checks, r);
                long t1 = System.currentTimeMillis();
                counts = new Verifier(checks, gameDir, true).verify(r);
                checkWorlds(checks, gameDir);
                durations.addProperty("verify_ms", System.currentTimeMillis() - t1);
            }
        } catch (Throwable t) {
            GregtecLink.LOG.error("client harness failed", t);
            check(checks, "harness_ran", false, Json.error(t));
        }
        clientDurations = durations;
        clientCounts = counts;
    }

    /**
     * Called by {@link ExportClient} on the next tick, once it has left the world and just before
     * {@code Minecraft.shutdown()}: the integrated server must be gone, and it must have saved the world after the run
     * finished. Writes the result.
     */
    public static void onClientShutdown(File gameDir, boolean serverStopped) {
        clientActive = false;
        List<JsonObject> checks = clientChecks;
        check(
            checks,
            "server_stopped_before_shutdown",
            serverStopped,
            serverStopped ? "the integrated server had stopped" : "the integrated server was still running");
        Path level = gameDir.toPath()
            .resolve("saves/" + AutoExport.WORLD_FOLDER + "/level.dat");
        long saved = -1;
        try {
            saved = Files.getLastModifiedTime(level)
                .toMillis();
        } catch (IOException e) {
            // Reported below.
        }
        check(
            checks,
            "world_saved_on_quit",
            saved >= clientFinishedMs,
            saved < 0 ? level + " missing"
                : "level.dat written " + (saved - clientFinishedMs) + " ms after the run finished");
        boolean pass = writeResult(gameDir, checks, clientRunId, clientDurations, clientCounts);
        GregtecLink.LOG.info("client harness {}: quitting", pass ? "PASSED" : "FAILED");
    }

    /** Writes harness-result.json and logs each check; true if every check passed and the file was written. */
    private static boolean writeResult(File gameDir, List<JsonObject> checks, String runId, JsonObject durations,
        JsonElement counts) {
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
            PathGuard guard = new PathGuard(gameDir.toPath(), "gregteclink");
            Files.write(guard.resolve(RESULT), (Json.PRETTY.toJson(result) + "\n").getBytes(StandardCharsets.UTF_8));
        } catch (Throwable t) {
            GregtecLink.LOG.error("harness: cannot write " + RESULT, t);
            pass = false;
        }
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
        return pass;
    }

    /** The exit code is a second signal; harness-result.json is the contract. */
    public static void onServerStopped() {
        if (ran) {
            FMLCommonHandler.instance()
                .exitJava(exitCode, false);
        }
    }

    /**
     * Old runs for the pruning check (D98), all older than the harness run: {@link #SEEDED_COMPLETE} complete ones,
     * {@code KEEP_INCOMPLETE + 1} incomplete ones and one stale {@code .partial}.
     */
    private static void seedOldRuns(File gameDir) throws IOException {
        PathGuard guard = new PathGuard(gameDir.toPath(), ExportRun.EXPORTS_DIR);
        for (int i = 0; i < SEEDED_COMPLETE; i++) {
            seed(guard, String.format("seed-%02d", i), true, String.format("2000-01-01T00:00:%02dZ", i));
        }
        for (int i = 0; i <= ExportRun.KEEP_INCOMPLETE; i++) { // one more than are kept
            seed(guard, String.format("seed-incomplete-%d", i), false, String.format("1999-01-01T00:00:%02dZ", i));
        }
        seed(guard, "seed-stale.partial", true, "2000-01-02T00:00:00Z");
    }

    private static void seed(PathGuard guard, String name, boolean complete, String created) throws IOException {
        JsonObject m = new JsonObject();
        m.addProperty("format", ExportRun.FORMAT);
        m.addProperty("mode", "harness");
        m.addProperty("complete", complete);
        m.addProperty("created_utc", created);
        Files.createDirectory(guard.resolve(name));
        Files.write(
            guard.resolve(name + "/manifest.json"),
            Json.COMPACT.toJson(m)
                .getBytes(StandardCharsets.UTF_8));
    }

    /** After the export: the run, the newest 9 seeded complete runs and the newest 3 incomplete ones. */
    private static void checkPrune(List<JsonObject> checks, ExportRun.Result r) throws IOException {
        Set<String> want = new TreeSet<>();
        want.add(r.runId);
        for (int i = SEEDED_COMPLETE - (ExportRun.KEEP_COMPLETE - 1); i < SEEDED_COMPLETE; i++) {
            want.add(String.format("seed-%02d", i));
        }
        for (int i = 1; i <= ExportRun.KEEP_INCOMPLETE; i++) want.add(String.format("seed-incomplete-%d", i));
        Set<String> left = new TreeSet<>();
        if (r.dir != null) {
            try (DirectoryStream<Path> s = Files.newDirectoryStream(r.dir.getParent())) {
                for (Path p : s) left.add(
                    p.getFileName()
                        .toString());
            }
        }
        check(
            checks,
            "prune_keeps_newest_" + ExportRun.KEEP_COMPLETE + "_and_" + ExportRun.KEEP_INCOMPLETE + "_incomplete",
            left.equals(want),
            "exports/ holds " + left + (left.equals(want) ? "" : ", want " + want));
    }

    static void check(List<JsonObject> checks, String name, boolean ok, String detail) {
        JsonObject c = new JsonObject();
        c.addProperty("name", name);
        c.addProperty("ok", ok);
        c.addProperty("detail", detail == null ? "" : detail);
        checks.add(c);
    }

    /** relpath -> size:mtime for the game dir (config included), without the world, logs and crash reports. */
    private static Map<String, String> snapshot(File gameDir, String world) throws IOException {
        final Path root = gameDir.toPath()
            .toAbsolutePath()
            .normalize();
        final Set<String> skip = new HashSet<>(Arrays.asList(world, "logs", "crash-reports"));
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

    private static void checkWrites(List<JsonObject> checks, String name, Map<String, String> before,
        Map<String, String> after, List<String> allowed) {
        TreeSet<String> outside = new TreeSet<>();
        for (Map.Entry<String, String> e : after.entrySet()) {
            if (e.getValue()
                .equals(before.get(e.getKey()))) continue;
            if (!under(e.getKey(), allowed)) outside.add(e.getKey());
        }
        TreeSet<String> deleted = new TreeSet<>();
        for (String k : before.keySet()) {
            if (!after.containsKey(k) && !under(k, allowed)) deleted.add(k);
        }
        boolean ok = outside.isEmpty() && deleted.isEmpty();
        check(
            checks,
            name,
            ok,
            ok ? "no file outside " + allowed + " created, changed or deleted"
                : "changed: " + outside + " deleted: " + deleted);
    }

    /**
     * {@link #CLIENT_WRITES}, plus the file each CropsNH dumper wrote directly in {@code dumps/} (the crop step's
     * copies in the run's {@code crops/} name them).
     */
    private static List<String> clientWrites(ExportRun.Result r) throws IOException {
        List<String> out = new ArrayList<>(CLIENT_WRITES);
        Path crops = r == null || r.dir == null ? null : r.dir.resolve(CROPS_DIR);
        if (crops != null && Files.isDirectory(crops)) {
            try (DirectoryStream<Path> s = Files.newDirectoryStream(crops)) {
                for (Path p : s) out.add("dumps/" + p.getFileName());
            }
        }
        return out;
    }

    private static boolean under(String rel, List<String> prefixes) {
        for (String p : prefixes) {
            if (rel.startsWith(p)) return true;
        }
        return false;
    }

    /** The old marked autoexport world was rebuilt; a marked world with another name was left alone. */
    private static void checkWorlds(List<JsonObject> checks, File gameDir) throws IOException {
        Path saves = gameDir.toPath()
            .resolve("saves");
        Path world = saves.resolve(AutoExport.WORLD_FOLDER);
        boolean marker = Files.isRegularFile(world.resolve(AutoExport.MARKER));
        boolean level = Files.isRegularFile(world.resolve("level.dat"));
        boolean staleGone = !Files.exists(world.resolve(STALE_FILE));
        Set<String> copies = new TreeSet<>();
        try (DirectoryStream<Path> s = Files.newDirectoryStream(saves, AutoExport.OLD_PREFIX + "*")) {
            for (Path p : s) copies.add(
                p.getFileName()
                    .toString());
        }
        check(
            checks,
            "autoexport_world_rebuilt",
            marker && level && staleGone && copies.isEmpty(),
            "saves/" + AutoExport.WORLD_FOLDER
                + ": marker="
                + marker
                + " level.dat="
                + level
                + " seeded "
                + STALE_FILE
                + " gone="
                + staleGone
                + ", old copies left: "
                + copies);
        Path decoy = saves.resolve(DECOY_WORLD);
        Set<String> left = new TreeSet<>();
        if (Files.isDirectory(decoy)) {
            try (DirectoryStream<Path> s = Files.newDirectoryStream(decoy)) {
                for (Path p : s) left.add(
                    p.getFileName()
                        .toString());
            }
        }
        Set<String> want = new TreeSet<>(Arrays.asList(AutoExport.MARKER, DECOY_FILE));
        check(checks, "decoy_world_untouched", left.equals(want), "saves/" + DECOY_WORLD + " holds " + left);
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
        private int nbtStacks;
        private int nbtStacksUnnamed;
        private String nbtUnnamedExample;
        private int identityHashLines;
        private String identityHashExample;

        private final File gameDir;
        /** The client harness: the run has an NEI step and was made by autoexport. */
        private final boolean client;

        Verifier(List<JsonObject> checks, File gameDir, boolean client) {
            this.checks = checks;
            this.gameDir = gameDir;
            this.client = client;
        }

        JsonElement verify(ExportRun.Result r) throws IOException {
            Path dir = r.dir;
            boolean dirOk = dir != null && Files.isDirectory(dir)
                && !dir.getFileName()
                    .toString()
                    .endsWith(".partial")
                && !Files.exists(dir.resolveSibling(r.runId + ".partial"));
            check(checks, "run_dir_final", dirOk, dir + " (rename tries: " + r.renameAttempts + ")");
            if (!dirOk) return null;

            JsonObject manifest = document(dir.resolve("manifest.json")).getAsJsonObject();
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

            JsonArray maps = document(dir.resolve("gt_recipe_maps.json")).getAsJsonArray();
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
            eachLine(dir.resolve("gt_recipes.jsonl"), scan::recipe);
            check(checks, "blastfurnace_coil_heat", scan.coilHeat > 0, scan.coilHeat + " EBF recipes with coil_heat");
            check(checks, "recipe_owners_captured", scan.withOwners > 0, scan.withOwners + " recipes with owners");
            check(checks, "non_consumed_inputs_kept", scan.nZero > 0, scan.nZero + " inputs with n == 0");

            boolean[] ebf = { false };
            eachLine(dir.resolve("gt_machines.jsonl"), o -> {
                if (o.get("class")
                    .getAsString()
                    .endsWith(".MTEElectricBlastFurnace")
                    && o.get("meta")
                        .getAsInt() == 1000
                    && o.has("recipe_maps")
                    && o.get("recipe_maps")
                        .toString()
                        .contains("\"gt.recipe.blastfurnace\"")) {
                    ebf[0] = true;
                }
            });
            check(checks, "machine_ebf_1000", ebf[0], "MTEElectricBlastFurnace at gregtech:gt.blockmachines@1000");
            checkOreMixes(dir, counts, manifest.getAsJsonObject("parts"));

            Set<String> items = new HashSet<>();
            eachLine(dir.resolve("items.jsonl"), o -> items.add(refKey(o)));
            Set<String> fluids = new HashSet<>();
            eachLine(
                dir.resolve("fluids.jsonl"),
                o -> fluids.add(
                    o.get("fluid")
                        .getAsString()));

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
                forbidden == 0 ? "none of " + FORBIDDEN_KEYS + " outside NBT, in every file"
                    : forbidden + " found, e.g. " + forbiddenExample);
            check(
                checks,
                "nbt_item_ids_named",
                nbtStacks > 0 && nbtStacksUnnamed == 0,
                nbtStacks + " ItemStack-shaped NBT compounds, "
                    + nbtStacksUnnamed
                    + " without __item"
                    + (nbtUnnamedExample == null ? "" : ", e.g. " + nbtUnnamedExample));
            check(
                checks,
                "no_identity_hashes",
                identityHashLines == 0,
                identityHashLines == 0 ? "no pkg.Class@hex in any file"
                    : identityHashLines + " lines, e.g. " + identityHashExample);

            Set<String> missing = new TreeSet<>(referencedItems);
            missing.removeAll(items);
            Set<String> missingFluids = new TreeSet<>(referencedFluids);
            missingFluids.removeAll(fluids);
            check(
                checks,
                "ref_tables_cover_refs",
                missing.isEmpty() && missingFluids.isEmpty(),
                referencedItems.size() + " items (NBT variants included), "
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
            String auto = parts.get("autoexport")
                .getAsString();
            String side = manifest.get("side")
                .getAsString();
            String world = manifest.get("world")
                .isJsonNull() ? null
                    : manifest.get("world")
                        .getAsString();
            JsonElement errors = manifest.get("part_errors");
            if (!client) {
                // The dev runtime has NEI 2.8.172, so the writer is there; a server never calls it.
                check(checks, "nei_not_run_on_server", "skipped_server".equals(nei), "parts.nei=" + nei);
                // The dev runtime has CropsNH (WO-1.10); its dumpers need NEI's client, so a server never runs them.
                JsonElement crops = parts.get("crops");
                check(
                    checks,
                    "crops_not_run_on_server",
                    crops != null && "skipped_server".equals(crops.getAsString()),
                    "parts.crops=" + crops);
                check(checks, "autoexport_off", "off".equals(auto), "parts.autoexport=" + auto);
                return counts;
            }
            check(checks, "client_side", "client".equals(side), "side=" + side);
            check(
                checks,
                "autoexport_world",
                AutoExport.WORLD_FOLDER.equals(world),
                "world=" + world + ", want " + AutoExport.WORLD_FOLDER);
            check(
                checks,
                "autoexport_ok",
                "ok".equals(auto),
                "parts.autoexport=" + auto + (errors == null ? "" : " part_errors=" + errors));
            boolean neiFail = Boolean.getBoolean(NEI_FAIL_PROPERTY);
            checkCrops(dir, manifest, !neiFail);
            if (neiFail) {
                // -PharnessNeiFail: dumps/recipes is a file, so NEI's dump fails; NEI only logs it.
                String why = errors != null && errors.getAsJsonObject()
                    .has("nei") ? errors.getAsJsonObject()
                        .get("nei")
                        .getAsString() : "";
                check(
                    checks,
                    "nei_failure_detected",
                    "failed".equals(nei) && why.contains("Error dumping recipes"),
                    "parts.nei=" + nei + " part_errors.nei=" + why);
                return counts;
            }
            // The dev runtime's NEI drops no handler, so a warning fails the check here.
            JsonElement warnings = manifest.get("part_warnings");
            check(
                checks,
                "nei_ok",
                "ok".equals(nei) && warnings == null,
                "parts.nei=" + nei
                    + (errors == null ? "" : " part_errors=" + errors)
                    + (warnings == null ? "" : " part_warnings=" + warnings));
            checkNei(dir, manifest.getAsJsonObject("files"), counts);
            return counts;
        }

        /**
         * The crop step (WO-1.10): CropsNH's dumper files in {@code crops/}, listed and left in {@code dumps/}; the
         * crop
         * registry dump lists crops; and (V33) every crop NEI's CropsNH tabs show, by the crop id in its seed's NBT, is
         * in that dump. {@code withNei}: the NEI step was meant to succeed, so its CropsNH handler files are there.
         */
        private void checkCrops(Path dir, JsonObject manifest, boolean withNei) throws IOException {
            JsonObject parts = manifest.getAsJsonObject("parts");
            JsonObject counts = manifest.getAsJsonObject("counts");
            JsonObject files = manifest.getAsJsonObject("files");
            JsonElement errors = manifest.get("part_errors");
            JsonElement status = parts.get("crops");
            JsonElement warnings = manifest.get("part_warnings");
            JsonElement cropWarning = warnings != null && warnings.isJsonObject() ? warnings.getAsJsonObject()
                .get("crops") : null;
            Map<String, JsonObject> listed = new TreeMap<>();
            for (Map.Entry<String, JsonElement> e : files.entrySet()) {
                if (e.getKey()
                    .startsWith(CROPS_DIR))
                    listed.put(
                        e.getKey()
                            .substring(CROPS_DIR.length()),
                        e.getValue()
                            .getAsJsonObject());
            }
            int copied = counts.has("crop_files") ? counts.get("crop_files")
                .getAsInt() : -1;
            int dumpers = counts.has("crop_dumpers") ? counts.get("crop_dumpers")
                .getAsInt() : -1;
            check(
                checks,
                "crops_ok",
                status != null && "ok".equals(status.getAsString())
                    && copied == listed.size()
                    && copied == dumpers
                    && cropWarning == null,
                "parts.crops=" + status
                    + " crop_dumpers="
                    + dumpers
                    + " crop_files="
                    + copied
                    + " listed="
                    + listed.size()
                    + (errors == null ? "" : " part_errors=" + errors)
                    + (cropWarning == null ? "" : " part_warnings.crops=" + cropWarning));

            Set<String> onDisk = new TreeSet<>();
            Path crops = dir.resolve(CROPS_DIR);
            if (Files.isDirectory(crops)) {
                try (DirectoryStream<Path> s = Files.newDirectoryStream(crops)) {
                    for (Path p : s) onDisk.add(
                        p.getFileName()
                            .toString());
                }
            }
            Set<String> problems = new TreeSet<>();
            for (String name : CROP_FILES) {
                if (!listed.containsKey(name)) problems.add(name + " not listed");
            }
            for (String name : onDisk) {
                if (!listed.containsKey(name)) problems.add(name + " unlisted");
            }
            Path dumps = gameDir.toPath()
                .resolve("dumps");
            for (Map.Entry<String, JsonObject> e : listed.entrySet()) {
                Path p = dumps.resolve(e.getKey());
                if (!Files.isRegularFile(p) || Files.size(p) != e.getValue()
                    .get("bytes")
                    .getAsLong()) problems.add("dumps/" + e.getKey() + " missing or changed");
            }
            check(
                checks,
                "crop_files_listed",
                problems.isEmpty(),
                problems.isEmpty() ? listed.keySet() + " listed, each still in dumps/" : first(problems));

            // The crop registry: a CSV with a header row, one crop per line, the crop id first (CropsNH's
            // CropRegistry.dumpCrops). Crop ids carry no comma or quote, so the first field is the id as is.
            Set<String> ids = new TreeSet<>();
            boolean header = false;
            Path registry = crops.resolve("cropRegistry.csv");
            if (Files.isRegularFile(registry)) {
                String charset = manifest.getAsJsonObject("java")
                    .get("charset")
                    .getAsString();
                List<String> lines = Files.readAllLines(registry, Charset.forName(charset));
                header = !lines.isEmpty() && lines.get(0)
                    .startsWith("ID,Name,Unlocalised Name,");
                for (int i = 1; i < lines.size(); i++) {
                    String l = lines.get(i);
                    int comma = l.indexOf(',');
                    if (comma > 0 && l.indexOf(':') > 0 && l.indexOf(':') < comma) ids.add(l.substring(0, comma));
                }
            }
            check(
                checks,
                "crop_registry_lists_crops",
                header && ids.size() >= MIN_CROPS,
                "header=" + header + ", " + ids.size() + " crop ids (want >= " + MIN_CROPS + "), e.g. " + first(ids));
            if (!withNei) return;

            CropNeiScan scan = new CropNeiScan();
            for (Map.Entry<String, JsonElement> e : files.entrySet()) {
                if (e.getKey()
                    .startsWith("nei/")) scan.file(dir.resolve(e.getKey()));
            }
            Set<String> unknown = new TreeSet<>(scan.crops);
            unknown.removeAll(ids);
            check(
                checks,
                "crop_nei_tabs_covered",
                scan.breeding > 0 && scan.cropPages > 0 && !scan.crops.isEmpty() && unknown.isEmpty(),
                scan.breeding + " Crop Breeding recipes, "
                    + scan.cropPages
                    + " crop pages, "
                    + scan.crops.size()
                    + " crop ids in NEI's CropsNH tabs; not in cropRegistry.csv: "
                    + first(unknown));
        }

        /** NEI's lines of CropsNH's handlers: the crop id ({@code tag.crop}) of every CropsNH seed in them. */
        private final class CropNeiScan {

            final Set<String> crops = new TreeSet<>();
            int breeding;
            int cropPages;

            void file(Path p) throws IOException {
                try (BufferedReader in = Files.newBufferedReader(p, StandardCharsets.UTF_8)) {
                    String line;
                    while ((line = in.readLine()) != null) {
                        if (!line.contains(CROP_HANDLER_PREFIX) && !line.contains("\"mutationPool\"")) continue;
                        JsonObject o = parser.parse(line)
                            .getAsJsonObject();
                        String h = o.get("handler")
                            .getAsString();
                        if (h.equals(CROP_HANDLER_PREFIX + "Breeding")) breeding++;
                        else if (h.equals(CROP_HANDLER_PREFIX + "Crops")) cropPages++;
                        else if (!h.startsWith(CROP_HANDLER_PREFIX) && !h.equals("mutationPool")) continue;
                        for (String k : new String[] { "ingredients", "results" }) {
                            if (o.has(k)) for (JsonElement s : o.getAsJsonArray(k)) stack(s.getAsJsonObject());
                        }
                    }
                }
            }

            private void stack(JsonObject s) {
                if (s.has("tag") && s.get("tag")
                    .isJsonObject()) {
                    JsonElement crop = s.getAsJsonObject("tag")
                        .get("crop");
                    if (crop != null && crop.isJsonPrimitive()) crops.add(crop.getAsString());
                }
                if (s.has("anyOf")) for (JsonElement a : s.getAsJsonArray("anyOf")) stack(a.getAsJsonObject());
            }
        }

        /** gt_ore_mixes.json (D126): GT's ore layers with four keyed ores each, and the vanilla dimensions named. */
        private void checkOreMixes(Path dir, JsonObject counts, JsonObject parts) throws IOException {
            JsonElement status = parts.get("ore_mixes");
            check(
                checks,
                "ore_mixes_ok",
                status != null && "ok".equals(status.getAsString())
                    && counts.get("ore_mix_errors")
                        .getAsInt() == 0,
                "parts.ore_mixes=" + status + " ore_mix_errors=" + counts.get("ore_mix_errors"));
            JsonObject root = document(dir.resolve("gt_ore_mixes.json")).getAsJsonObject();
            int layers = 0;
            int keyed = 0;
            boolean naquadah = false;
            for (JsonElement e : root.getAsJsonArray("layers")) {
                JsonObject l = e.getAsJsonObject();
                layers++;
                int ores = 0;
                JsonObject o = l.getAsJsonObject("ores");
                for (String layer : new String[] { "primary", "secondary", "between", "sporadic" }) {
                    JsonObject ore = o.has(layer) ? o.getAsJsonObject(layer) : null;
                    if (ore != null && ore.get("material")
                        .isJsonPrimitive()
                        && ore.has("ore")
                        && ore.getAsJsonObject("ore")
                            .get("item")
                            .isJsonPrimitive())
                        ores++;
                }
                if (ores == 4) keyed++;
                if ("ore.mix.naquadah".equals(
                    l.get("name")
                        .getAsString())) {
                    naquadah = l.get("weight")
                        .getAsInt() > 0
                        && l.get("min_y")
                            .getAsInt()
                            < l.get("max_y")
                                .getAsInt()
                        && l.getAsJsonArray("dims")
                            .size() > 0
                        && ores == 4;
                }
            }
            check(
                checks,
                "ore_mixes_keyed",
                layers >= 100 && keyed == layers
                    && layers == counts.get("ore_mixes")
                        .getAsInt(),
                layers + " ore layers, " + keyed + " with four ores keyed by material and ore block");
            check(checks, "ore_mix_naquadah", naquadah, "ore.mix.naquadah with weight, heights, dimensions and ores");
            Map<Integer, String> dims = new TreeMap<>();
            for (JsonElement e : root.getAsJsonArray("dimensions")) {
                JsonObject d = e.getAsJsonObject();
                if (d.has("name") && d.get("name")
                    .isJsonPrimitive())
                    dims.put(
                        d.get("dim")
                            .getAsInt(),
                        d.get("name")
                            .getAsString());
            }
            check(
                checks,
                "dimensions_named",
                "Overworld".equals(dims.get(0)) && "Nether".equals(dims.get(-1)) && "The End".equals(dims.get(1)),
                dims.toString());
        }

        /** NEI's files in the run: listed, complete, one handler each, GT handlers present, no numeric IDs. */
        private void checkNei(Path dir, JsonObject files, JsonObject counts) throws IOException {
            Map<String, JsonObject> listed = new TreeMap<>();
            for (Map.Entry<String, JsonElement> e : files.entrySet()) {
                if (e.getKey()
                    .startsWith("nei/"))
                    listed.put(
                        e.getKey()
                            .substring(4),
                        e.getValue()
                            .getAsJsonObject());
            }
            Set<String> onDisk = new TreeSet<>();
            Path nei = dir.resolve("nei");
            if (Files.isDirectory(nei)) {
                try (DirectoryStream<Path> s = Files.newDirectoryStream(nei)) {
                    for (Path p : s) onDisk.add(
                        p.getFileName()
                            .toString());
                }
            }
            Set<String> unlisted = new TreeSet<>(onDisk);
            unlisted.removeAll(listed.keySet());
            check(
                checks,
                "nei_files_listed",
                !listed.isEmpty() && unlisted.isEmpty(),
                listed.size() + " nei/ files listed, unlisted on disk: " + first(unlisted));
            long handlers = counts.has("nei_handlers") ? counts.get("nei_handlers")
                .getAsLong() : -1;
            check(
                checks,
                "nei_handler_files_at_least_150",
                listed.size() >= 150 && listed.size() == handlers,
                listed.size() + " files, NEI loaded " + handlers + " handlers");
            long lines = 0;
            for (JsonObject f : listed.values()) lines += f.get("lines")
                .getAsLong();
            long recipes = counts.has("nei_recipes") ? counts.get("nei_recipes")
                .getAsLong() : -1;
            long reported = counts.has("nei_reported") ? counts.get("nei_reported")
                .getAsLong() : -1;
            check(
                checks,
                "nei_lines_equal_reported",
                lines == recipes && recipes == reported && recipes > 50_000,
                lines + " lines listed, nei_recipes=" + recipes + ", nei_reported=" + reported + " (want > 50000)");

            NeiScan scan = new NeiScan();
            for (String name : listed.keySet()) scan.file(nei.resolve(name));
            check(
                checks,
                "nei_one_handler_per_file",
                scan.mixedFiles.isEmpty(),
                scan.handlers.size() + " handler ids, "
                    + scan.emptyFiles
                    + " empty files; mixed: "
                    + first(scan.mixedFiles));
            check(
                checks,
                "nei_gt_handlers",
                scan.gtHandlers > 0,
                scan.gtHandlers + " gt.recipe.* handler ids, e.g. " + scan.gtExample);
            check(
                checks,
                "nei_no_numeric_ids",
                scan.forbidden == 0,
                scan.forbidden == 0 ? scan.stacks + " stacks (anyOf and returns included), none with " + FORBIDDEN_KEYS
                    : scan.forbidden + " found, e.g. " + scan.forbiddenExample);

            // GregtecLink copies NEI's files and never moves or changes them.
            List<String> gone = new ArrayList<>();
            Path dumps = gameDir.toPath()
                .resolve("dumps/recipes");
            for (Map.Entry<String, JsonObject> e : listed.entrySet()) {
                Path p = dumps.resolve(e.getKey());
                if (!Files.isRegularFile(p) || Files.size(p) != e.getValue()
                    .get("bytes")
                    .getAsLong()) gone.add(e.getKey());
            }
            check(
                checks,
                "nei_dumps_left_in_place",
                gone.isEmpty(),
                gone.isEmpty() ? listed.size() + " files still in dumps/recipes"
                    : "missing or changed: " + first(new TreeSet<>(gone)));
        }

        /** NEI's lines: {handler, ingredients, results, gt?}; stacks carry strId, never a numeric id. */
        private final class NeiScan {

            final Set<String> handlers = new TreeSet<>();
            final Set<String> mixedFiles = new TreeSet<>();
            int emptyFiles;
            int gtHandlers;
            String gtExample;
            int stacks;
            int forbidden;
            String forbiddenExample;

            void file(Path p) throws IOException {
                Set<String> inFile = new HashSet<>();
                try (BufferedReader in = Files.newBufferedReader(p, StandardCharsets.UTF_8)) {
                    String line;
                    while ((line = in.readLine()) != null) {
                        JsonObject o = parser.parse(line)
                            .getAsJsonObject();
                        String h = o.get("handler")
                            .getAsString();
                        inFile.add(h);
                        if (handlers.add(h) && h.startsWith("gt.recipe")) {
                            gtHandlers++;
                            if (gtExample == null) gtExample = h;
                        }
                        for (String k : new String[] { "ingredients", "results" }) {
                            if (o.has(k)) for (JsonElement s : o.getAsJsonArray(k)) stack(s.getAsJsonObject(), true);
                        }
                    }
                }
                if (inFile.isEmpty()) emptyFiles++;
                if (inFile.size() > 1) mixedFiles.add(
                    p.getFileName()
                        .toString());
            }

            /** Keys of the stack itself; its "tag" is game NBT and is not searched. */
            private void stack(JsonObject s, boolean slot) {
                stacks++;
                for (Map.Entry<String, JsonElement> f : s.entrySet()) {
                    if (!FORBIDDEN_KEYS.contains(f.getKey())) continue;
                    if (forbidden++ == 0) forbiddenExample = f.getKey() + " in " + s;
                }
                if (!slot) return;
                if (s.has("anyOf")) for (JsonElement a : s.getAsJsonArray("anyOf")) stack(a.getAsJsonObject(), false);
                if (s.has("returns")) stack(s.getAsJsonObject("returns"), false);
            }
        }

        private void checkFiles(Path dir, JsonObject files) throws IOException {
            List<String> bad = new ArrayList<>();
            String[] required = { "gt_recipe_maps.json", "gt_recipes.jsonl", "gt_machines.jsonl", "gt_ore_mixes.json",
                "items.jsonl", "fluids.jsonl" };
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

        /** A whole JSON file: scanned for identity hashes and walked. */
        private JsonElement document(Path p) throws IOException {
            String text = new String(Files.readAllBytes(p), StandardCharsets.UTF_8);
            for (String line : text.split("\n")) identityHashes(line);
            JsonElement e = parser.parse(text);
            walk(e);
            return e;
        }

        /** A JSONL file: each line scanned for identity hashes, walked, then handed on. */
        private void eachLine(Path p, Consumer<JsonObject> then) throws IOException {
            try (BufferedReader in = Files.newBufferedReader(p, StandardCharsets.UTF_8)) {
                String line;
                while ((line = in.readLine()) != null) {
                    identityHashes(line);
                    JsonObject o = parser.parse(line)
                        .getAsJsonObject();
                    walk(o);
                    then.accept(o);
                }
            }
        }

        private void identityHashes(String line) {
            String clean = Json.stripIdentityHashes(line);
            if (clean.equals(line)) return;
            if (identityHashLines++ == 0) {
                int i = 0;
                while (i < clean.length() && clean.charAt(i) == line.charAt(i)) i++;
                identityHashExample = line.substring(Math.max(0, i - 80), Math.min(line.length(), i + 20));
            }
        }

        /** "item@meta", plus " <nbt json>" for a ref with NBT. */
        private static String refKey(JsonObject o) {
            return o.get("item")
                .getAsString() + "@"
                + o.get("meta")
                    .getAsInt()
                + (o.has("nbt") ? " " + o.get("nbt") : "");
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
                    if (k.equals("nbt")) {
                        nbt(f.getValue());
                        continue;
                    }
                    if (k.equals("item")) {
                        JsonElement v = f.getValue();
                        if (v.isJsonPrimitive() && v.getAsJsonPrimitive()
                            .isString()) {
                            if (o.has("meta")) referencedItems.add(refKey(o));
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

        /** Inside NBT, numeric IDs are game data; each ItemStack-shaped compound must also carry "__item". */
        private void nbt(JsonElement e) {
            if (e.isJsonArray()) {
                for (JsonElement c : e.getAsJsonArray()) nbt(c);
            } else if (e.isJsonObject()) {
                JsonObject o = e.getAsJsonObject();
                if (o.has("id") && o.has("Count") && o.has("Damage")) {
                    nbtStacks++;
                    JsonElement name = o.get("__item");
                    if (name == null || !name.isJsonPrimitive()) {
                        if (nbtStacksUnnamed++ == 0) nbtUnnamedExample = o.toString();
                    }
                }
                for (Map.Entry<String, JsonElement> f : o.entrySet()) nbt(f.getValue());
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
