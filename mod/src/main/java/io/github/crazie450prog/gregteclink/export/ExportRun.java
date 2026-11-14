package io.github.crazie450prog.gregteclink.export;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TimeZone;
import java.util.regex.Pattern;

import net.minecraft.server.MinecraftServer;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import io.github.crazie450prog.gregteclink.GregtecLink;

/**
 * One export run: writes every file into {@code <run_id>.partial/} (the GT parts at server start, then NEI's files on a
 * client), writes manifest.json last, then renames the folder to {@code <run_id>/} and prunes old runs. Everything goes
 * through a {@link PathGuard} rooted at {@code <gameDir>/gregteclink/exports/}. It reads the game and never changes the
 * world.
 */
public final class ExportRun {

    public static final String FORMAT = "gregteclink-export/1";
    public static final String EXPORTS_DIR = "gregteclink/exports";
    public static final String RUN_ID_PROPERTY = "gregtec.runId";
    private static final Pattern RUN_ID = Pattern.compile("[A-Za-z0-9][A-Za-z0-9._-]{0,63}");
    /** On Windows a virus scanner or the indexer often still holds a just-written file, so the rename is retried. */
    static final int RENAME_ATTEMPTS = 3;
    static final long RENAME_BACKOFF_MS = 1000;
    /** Complete runs kept in the exports folder (D98); each is about 50 MB, about 145 MB with NEI's files. */
    public static final int KEEP_COMPLETE = 10;
    /** Incomplete runs (manifest "complete": false) kept for debugging (D98). */
    public static final int KEEP_INCOMPLETE = 3;

    /** What the caller (one-click, autoexport, harness) needs to know afterwards. */
    public static final class Result {

        public final String runId;
        public final Path dir;
        public final boolean complete;
        /** Tries the .partial rename took; 0 if it failed or was never tried. */
        public final int renameAttempts;

        Result(String runId, Path dir, boolean complete, int renameAttempts) {
            this.runId = runId;
            this.dir = dir;
            this.complete = complete;
            this.renameAttempts = renameAttempts;
        }
    }

    final File gameDir;
    final String mode;
    final String runId;
    /** The world's folder name, from the server at start; null if there was none. */
    final String world;
    final Date created = new Date();
    final Map<String, String> parts = new LinkedHashMap<>();
    /** Why a part failed, for each failed part (manifest "part_errors"). */
    final Map<String, String> partErrors = new LinkedHashMap<>();
    /** What an ok part left out (manifest "part_warnings"). */
    final Map<String, String> partWarnings = new LinkedHashMap<>();
    final Map<String, Long> durations = new LinkedHashMap<>();
    final Map<String, Number> counts = new LinkedHashMap<>();
    final List<HashedFile> files = new ArrayList<>();
    /** The crop step's status ({@link CropTrigger#dump}); null if it did not run. */
    String crops;
    /** The palette pass's status ({@link PalettePass#run}, WO-10.3); null if it did not run. */
    String palette;
    /** Null if the exports folder could not be set up: then nothing is written. */
    private final PathGuard guard;
    private final long t0;
    private boolean gtOk;
    private Result result;

    private ExportRun(File gameDir, String mode, String runId, String world, PathGuard guard, long t0) {
        this.gameDir = gameDir;
        this.mode = mode;
        this.runId = runId;
        this.world = world;
        this.guard = guard;
        this.t0 = t0;
    }

    /** A whole export with no NEI step (a dedicated server, or a client without NEI's writer). Never throws. */
    public static Result run(File gameDir, String mode) {
        return start(gameDir, mode).finish(NeiTrigger.serverStatus(), "off");
    }

    /**
     * Starts a run on the server thread: writes the GT parts (recipe maps, machines, item and fluid tables) into
     * {@code <run_id>.partial/}. {@link #finish} then writes the manifest, renames and prunes; in between, a client
     * adds NEI's files ({@link #addNeiFile}). Never throws.
     */
    public static ExportRun start(File gameDir, String mode) {
        long t0 = System.currentTimeMillis();
        String requested = System.getProperty(RUN_ID_PROPERTY);
        String base;
        if (requested != null && RUN_ID.matcher(requested)
            .matches()) {
            base = requested;
        } else {
            if (requested != null) GregtecLink.LOG.warn("ignoring invalid -D{}={}", RUN_ID_PROPERTY, requested);
            SimpleDateFormat f = new SimpleDateFormat("yyyyMMdd'T'HHmmss'Z'");
            f.setTimeZone(TimeZone.getTimeZone("UTC"));
            base = f.format(new Date());
        }
        String world = null;
        try {
            MinecraftServer server = MinecraftServer.getServer();
            if (server != null) world = server.getFolderName();
        } catch (Throwable t) {
            GregtecLink.LOG.warn("export: world name not read: {}", t.toString());
        }
        PathGuard guard;
        try {
            guard = new PathGuard(gameDir.toPath(), EXPORTS_DIR);
        } catch (Throwable t) {
            GregtecLink.LOG.error("export: cannot create " + EXPORTS_DIR, t);
            return new ExportRun(gameDir, mode, base, world, null, t0);
        }
        String runId = base;
        for (int i = 2; Files.exists(
            guard.root()
                .resolve(runId))
            || Files.exists(
                guard.root()
                    .resolve(runId + ".partial")); i++) {
            runId = base + "-" + i;
        }
        ExportRun run = new ExportRun(gameDir, mode, runId, world, guard, t0);
        GregtecLink.LOG.info("export {} ({}) starting", runId, mode);
        run.gtOk = run.writeGt();
        return run;
    }

    /**
     * Copies one of NEI's dump files into {@code <run_id>.partial/nei/}, listed in the manifest as
     * {@code nei/<name>}. A failed copy is deleted again and not listed.
     */
    HashedFile addNeiFile(Path source) throws IOException {
        return addCopiedFile(
            "nei/" + source.getFileName()
                .toString(),
            source);
    }

    /** A new file written by a client step into {@code <run_id>.partial/<name>}, listed in the manifest. */
    HashedFile newFile(String name) throws IOException {
        if (guard == null) throw new IOException("no exports folder");
        return open(runId + ".partial", name);
    }

    /**
     * Copies another program's file, byte for byte, into {@code <run_id>.partial/<name>}, listed in the manifest as
     * {@code name}. A failed copy is deleted again and not listed.
     */
    HashedFile addCopiedFile(String name, Path source) throws IOException {
        if (guard == null) throw new IOException("no exports folder");
        String dir = runId + ".partial";
        HashedFile f = new HashedFile(guard, dir, name);
        try {
            try (InputStream in = Files.newInputStream(source)) {
                f.copy(in);
            }
            f.close();
        } catch (IOException | RuntimeException e) {
            try {
                f.close();
            } catch (IOException ignored) {
                // The copy failed already.
            }
            Files.deleteIfExists(guard.resolve(dir + "/" + name));
            throw e;
        }
        files.add(f);
        return f;
    }

    /**
     * Writes the manifest with the given NEI and autoexport status, renames the folder and prunes old runs. Runs once:
     * later calls return the first result. Never throws: a failed run still gets a manifest with "complete": false
     * where possible.
     */
    public synchronized Result finish(String nei, String autoexport) {
        if (result != null) return result;
        if (guard == null) {
            result = new Result(runId, null, false, 0);
            return result;
        }
        parts.put("nei", nei);
        // Not part of "complete" either (WO-1.10): `gos db build` checks the part before it reads crops/.
        parts.put("crops", crops != null ? crops : CropTrigger.notRun(this));
        // Not part of "complete" either (WO-10.3): `gos db build` loads the block catalog only when it is "ok".
        parts.put("palette", palette != null ? palette : PalettePass.notRun(this));
        parts.put("autoexport", autoexport);
        boolean complete = gtOk;
        // Last, and not listed in its own "files".
        try (HashedFile manifest = new HashedFile(guard, runId + ".partial", "manifest.json")) {
            manifest.document(new Manifest(this).build(complete));
        } catch (Throwable e) {
            GregtecLink.LOG.error("export: manifest failed", e);
            complete = false;
        }
        Path finalDir = null;
        int attempts = 0;
        try {
            Path partial = guard.resolve(runId + ".partial");
            finalDir = guard.resolve(runId);
            attempts = rename(partial, finalDir, RENAME_BACKOFF_MS);
        } catch (Throwable t) {
            if (t instanceof InterruptedException) Thread.currentThread()
                .interrupt();
            GregtecLink.LOG.error(
                "export " + runId + ": rename from .partial failed; readers skip the run until it is renamed by hand",
                t);
            complete = false;
        }
        if (attempts > 0) prune(guard, runId, KEEP_COMPLETE, KEEP_INCOMPLETE);
        GregtecLink.LOG.info(
            "export {} finished in {} ms: complete={} rename_tries={} counts={} parts={} errors={} warnings={} -> {}",
            runId,
            System.currentTimeMillis() - t0,
            complete,
            attempts,
            counts,
            parts,
            partErrors,
            partWarnings,
            finalDir);
        result = new Result(runId, finalDir, complete, attempts);
        return result;
    }

    /**
     * Deletes the complete runs (manifest {@code "complete": true}) beyond the newest {@code keep}, the incomplete
     * runs (manifest {@code "complete": false}) beyond the newest {@code keepIncomplete}, each ordered by manifest
     * {@code created_utc} and then name, and every {@code .partial} folder. Only folders directly in the guard's root,
     * and never {@code current}, which counts as one of the kept runs of its kind. Runs whose manifest is missing or
     * unreadable are kept. A run is renamed to {@code <name>.partial} before anything in it is deleted. A link or
     * junction is deleted itself, never followed. Never throws: what it can't delete stays (a run
     * that can't be renamed stays whole) and is logged. Returns the names it deleted.
     */
    static List<String> prune(PathGuard guard, String current, int keep, int keepIncomplete) {
        List<String[]> complete = new ArrayList<>(); // {created_utc, name}
        List<String[]> incomplete = new ArrayList<>();
        List<String> doomed = new ArrayList<>();
        try (DirectoryStream<Path> entries = Files.newDirectoryStream(guard.root())) {
            for (Path p : entries) {
                String name = p.getFileName()
                    .toString();
                if (!Files.isDirectory(p, LinkOption.NOFOLLOW_LINKS)) continue;
                if (name.endsWith(".partial")) {
                    if (!name.equals(current + ".partial")) doomed.add(name);
                    continue;
                }
                String[] state = manifestState(p.resolve("manifest.json")); // {complete, created_utc} or null
                if (state != null)
                    (state[0].equals("true") ? complete : incomplete).add(new String[] { state[1], name });
            }
        } catch (Throwable t) {
            GregtecLink.LOG.warn("export: old runs not pruned: {}", t.toString());
            return new ArrayList<>();
        }
        doomed.addAll(beyondNewest(complete, keep, current));
        doomed.addAll(beyondNewest(incomplete, keepIncomplete, current));
        List<String> deleted = new ArrayList<>();
        for (String name : doomed) { // .partial folders come first, so a run's .partial name is free by its turn
            try {
                // A complete run is renamed to .partial first, in one atomic step: if a file in it can't be deleted
                // (Windows: the indexer, a virus scanner or the collector still holds it), what is left is a .partial
                // folder that readers skip and the next prune deletes, never a "complete" run with files missing.
                String gone = name.endsWith(".partial") ? name : name + ".partial";
                if (!gone.equals(name)) {
                    Files.move(guard.resolve(name), guard.resolve(gone), StandardCopyOption.ATOMIC_MOVE);
                }
                deleteTree(guard.resolve(gone));
                deleted.add(name);
            } catch (Throwable t) {
                GregtecLink.LOG.warn("export: old run {} not deleted: {}", name, t.toString());
            }
        }
        if (!deleted.isEmpty()) GregtecLink.LOG.info("export: deleted old runs {}", deleted);
        return deleted;
    }

    /** The names of {@code runs} ({created_utc, name}) past the newest {@code keep}, never {@code current}. */
    private static List<String> beyondNewest(List<String[]> runs, int keep, String current) {
        Collections.sort(runs, new Comparator<String[]>() {

            @Override
            public int compare(String[] a, String[] b) {
                int c = b[0].compareTo(a[0]); // newest first
                return c != 0 ? c : b[1].compareTo(a[1]);
            }
        });
        List<String> out = new ArrayList<>();
        for (int i = keep; i < runs.size(); i++) {
            if (!runs.get(i)[1].equals(current)) out.add(runs.get(i)[1]);
        }
        return out;
    }

    /** {"true"|"false", created_utc or ""} from a readable manifest with a boolean "complete"; else null. */
    private static String[] manifestState(Path manifest) {
        try {
            if (!Files.isRegularFile(manifest, LinkOption.NOFOLLOW_LINKS)) return null;
            JsonObject m = new JsonParser().parse(new String(Files.readAllBytes(manifest), StandardCharsets.UTF_8))
                .getAsJsonObject();
            JsonElement complete = m.get("complete");
            if (complete == null || !complete.isJsonPrimitive()
                || !complete.getAsJsonPrimitive()
                    .isBoolean())
                return null;
            JsonElement created = m.get("created_utc");
            String when = created != null && created.isJsonPrimitive() ? created.getAsString() : "";
            return new String[] { String.valueOf(complete.getAsBoolean()), when };
        } catch (Throwable t) {
            return null;
        }
    }

    /** Deletes {@code p} and, if it is a real folder (not a link or junction), everything in it first. */
    static void deleteTree(Path p) throws IOException {
        if (Files.isDirectory(p, LinkOption.NOFOLLOW_LINKS) && p.toRealPath()
            .equals(p)) {
            try (DirectoryStream<Path> children = Files.newDirectoryStream(p)) {
                for (Path c : children) deleteTree(c);
            }
        }
        Files.delete(p);
    }

    /**
     * Moves {@code from} to {@code to} atomically, in up to three tries with a doubling wait ({@code backoffMs}, then
     * twice that). Returns the tries it took, or throws the last failure.
     */
    static int rename(Path from, Path to, long backoffMs) throws IOException, InterruptedException {
        for (int attempt = 1;; attempt++) {
            try {
                Files.move(from, to, StandardCopyOption.ATOMIC_MOVE);
                return attempt;
            } catch (IOException e) {
                if (attempt >= RENAME_ATTEMPTS) throw e;
                GregtecLink.LOG.warn("rename of {} failed (try {}): {}; retrying", from, attempt, e.toString());
                Thread.sleep(backoffMs << (attempt - 1));
            }
        }
    }

    /** The GT parts; true if the recipe dump, machine pass and item/fluid tables succeeded. */
    private boolean writeGt() {
        String dir = runId + ".partial";
        boolean ok = true;
        RefTable refs = new RefTable();
        StackJson stacks = new StackJson(refs);

        long t = System.currentTimeMillis();
        GtRecipeDump gt = new GtRecipeDump(stacks);
        try (HashedFile maps = open(dir, "gt_recipe_maps.json"); HashedFile recipes = open(dir, "gt_recipes.jsonl")) {
            gt.write(maps, recipes);
            parts.put("gt_dump", "ok");
        } catch (Throwable e) {
            GregtecLink.LOG.error("export: GT recipe dump failed", e);
            parts.put("gt_dump", "failed");
            partErrors.put("gt_dump", Json.error(e));
            ok = false;
        }
        durations.put("gt_dump", System.currentTimeMillis() - t);
        counts.put("recipe_maps", gt.maps);
        counts.put("recipe_map_errors", gt.mapErrors);
        counts.put("recipes", gt.recipes);
        counts.put("recipe_errors", gt.recipeErrors);

        t = System.currentTimeMillis();
        ModSources mods = new ModSources();
        MachinePass mp = new MachinePass(mods, refs);
        try (HashedFile machines = open(dir, "gt_machines.jsonl")) {
            mp.write(machines);
            parts.put("machines", "ok");
        } catch (Throwable e) {
            GregtecLink.LOG.error("export: machine pass failed", e);
            parts.put("machines", "failed");
            partErrors.put("machines", Json.error(e));
            ok = false;
        }
        durations.put("machines", System.currentTimeMillis() - t);
        counts.put("machines", mp.machines);
        counts.put("machine_errors", mp.machineErrors);

        // Not part of "complete" (as NEI's step): the recipe DB doesn't need it; `gos ores load` checks the part.
        t = System.currentTimeMillis();
        OreMixPass ores = new OreMixPass(stacks);
        try (HashedFile mixes = open(dir, "gt_ore_mixes.json")) {
            ores.write(mixes);
            parts.put("ore_mixes", "ok");
        } catch (Throwable e) {
            GregtecLink.LOG.error("export: ore mix pass failed", e);
            parts.put("ore_mixes", "failed");
            partErrors.put("ore_mixes", Json.error(e));
        }
        durations.put("ore_mixes", System.currentTimeMillis() - t);
        counts.put("ore_mixes", ores.layers);
        counts.put("ore_mix_errors", ores.layerErrors);
        counts.put("dimensions", ores.dimensions);

        // Turbine rotors and single-block generators (WO-4.1, D230). Not part of "complete": the recipe DB doesn't
        // need it; `gos db build` loads it only from runs with parts.power == "ok".
        t = System.currentTimeMillis();
        PowerPass power = new PowerPass(stacks);
        try (HashedFile out = open(dir, "gt_power.json")) {
            power.write(out);
            parts.put("power", "ok");
        } catch (Throwable e) {
            GregtecLink.LOG.error("export: power pass failed", e);
            parts.put("power", "failed");
            partErrors.put("power", Json.error(e));
        }
        durations.put("power", System.currentTimeMillis() - t);
        counts.put("turbine_materials", power.materials);
        counts.put("generators", power.generators);
        counts.put("power_errors", power.errors);

        // The full ore dictionary (D145). Not part of "complete" either: without it `gos db build` names NEI slots
        // from items.jsonl's ore lists, as before. Before the item table, so every member is listed there.
        t = System.currentTimeMillis();
        OreDictPass oredict = new OreDictPass(stacks);
        try (HashedFile out = open(dir, "oredict.jsonl")) {
            oredict.write(out);
            parts.put("oredict", "ok");
        } catch (Throwable e) {
            GregtecLink.LOG.error("export: oredict pass failed", e);
            parts.put("oredict", "failed");
            partErrors.put("oredict", Json.error(e));
        }
        durations.put("oredict", System.currentTimeMillis() - t);
        counts.put("oredict", oredict.names);
        counts.put("oredict_members", oredict.members);
        counts.put("oredict_errors", oredict.errors);

        t = System.currentTimeMillis();
        try (HashedFile items = open(dir, "items.jsonl"); HashedFile fluids = open(dir, "fluids.jsonl")) {
            refs.writeItems(items);
            refs.writeFluids(fluids);
            parts.put("refs", "ok");
        } catch (Throwable e) {
            GregtecLink.LOG.error("export: item/fluid table failed", e);
            parts.put("refs", "failed");
            partErrors.put("refs", Json.error(e));
            ok = false;
        }
        durations.put("refs", System.currentTimeMillis() - t);
        counts.put("items", refs.itemCount());
        counts.put("item_errors", refs.itemErrors);
        counts.put("fluids", refs.fluidCount());
        counts.put("unregistered", refs.unregistered);
        return ok;
    }

    private HashedFile open(String dir, String name) throws IOException {
        HashedFile f = new HashedFile(guard, dir, name);
        files.add(f);
        return f;
    }
}
