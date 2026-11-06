package io.github.crazie450prog.gregteclink.export;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TimeZone;
import java.util.regex.Pattern;

import io.github.crazie450prog.gregteclink.GregtecLink;

/**
 * One export run: writes every file into {@code <run_id>.partial/}, writes manifest.json last, then renames the folder
 * to {@code <run_id>/}. Everything goes through a {@link PathGuard} rooted at {@code <gameDir>/gregteclink/exports/}.
 * It reads the game and never changes the world.
 */
public final class ExportRun {

    public static final String FORMAT = "gregteclink-export/1";
    public static final String EXPORTS_DIR = "gregteclink/exports";
    public static final String RUN_ID_PROPERTY = "gregtec.runId";
    private static final Pattern RUN_ID = Pattern.compile("[A-Za-z0-9][A-Za-z0-9._-]{0,63}");
    /** On Windows a virus scanner or the indexer often still holds a just-written file, so the rename is retried. */
    private static final int RENAME_ATTEMPTS = 3;
    private static final long RENAME_BACKOFF_MS = 1000;

    /** What the caller (one-click, harness) needs to know afterwards. */
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
    final Date created = new Date();
    final Map<String, String> parts = new LinkedHashMap<>();
    final Map<String, Long> durations = new LinkedHashMap<>();
    final Map<String, Number> counts = new LinkedHashMap<>();
    final List<HashedFile> files = new ArrayList<>();

    private ExportRun(File gameDir, String mode, String runId) {
        this.gameDir = gameDir;
        this.mode = mode;
        this.runId = runId;
    }

    /** Runs a whole export. Never throws: a failed run still gets a manifest with "complete": false where possible. */
    public static Result run(File gameDir, String mode) {
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
        PathGuard guard;
        try {
            guard = new PathGuard(gameDir.toPath(), EXPORTS_DIR);
        } catch (Throwable t) {
            GregtecLink.LOG.error("export: cannot create " + EXPORTS_DIR, t);
            return new Result(base, null, false, 0);
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
        ExportRun run = new ExportRun(gameDir, mode, runId);
        GregtecLink.LOG.info("export {} ({}) starting", runId, mode);
        boolean complete = run.writeAll(guard);
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
        GregtecLink.LOG.info(
            "export {} finished in {} ms: complete={} rename_tries={} counts={} parts={} -> {}",
            runId,
            System.currentTimeMillis() - t0,
            complete,
            attempts,
            run.counts,
            run.parts,
            finalDir);
        return new Result(runId, finalDir, complete, attempts);
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

    private boolean writeAll(PathGuard guard) {
        String dir = runId + ".partial";
        boolean ok = true;
        RefTable refs = new RefTable();
        StackJson stacks = new StackJson(refs);

        long t = System.currentTimeMillis();
        GtRecipeDump gt = new GtRecipeDump(stacks);
        try (HashedFile maps = open(guard, dir, "gt_recipe_maps.json");
            HashedFile recipes = open(guard, dir, "gt_recipes.jsonl")) {
            gt.write(maps, recipes);
            parts.put("gt_dump", "ok");
        } catch (Throwable e) {
            GregtecLink.LOG.error("export: GT recipe dump failed", e);
            parts.put("gt_dump", "failed");
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
        try (HashedFile machines = open(guard, dir, "gt_machines.jsonl")) {
            mp.write(machines);
            parts.put("machines", "ok");
        } catch (Throwable e) {
            GregtecLink.LOG.error("export: machine pass failed", e);
            parts.put("machines", "failed");
            ok = false;
        }
        durations.put("machines", System.currentTimeMillis() - t);
        counts.put("machines", mp.machines);
        counts.put("machine_errors", mp.machineErrors);

        t = System.currentTimeMillis();
        try (HashedFile items = open(guard, dir, "items.jsonl"); HashedFile fluids = open(guard, dir, "fluids.jsonl")) {
            refs.writeItems(items);
            refs.writeFluids(fluids);
            parts.put("refs", "ok");
        } catch (Throwable e) {
            GregtecLink.LOG.error("export: item/fluid table failed", e);
            parts.put("refs", "failed");
            ok = false;
        }
        durations.put("refs", System.currentTimeMillis() - t);
        counts.put("items", refs.itemCount());
        counts.put("item_errors", refs.itemErrors);
        counts.put("fluids", refs.fluidCount());
        counts.put("unregistered", refs.unregistered);

        parts.put("nei", NeiTrigger.status());
        parts.put("autoexport", AutoExport.status());

        // Last, and not listed in its own "files".
        try (HashedFile manifest = new HashedFile(guard, dir, "manifest.json")) {
            manifest.document(new Manifest(this).build(ok));
        } catch (Throwable e) {
            GregtecLink.LOG.error("export: manifest failed", e);
            ok = false;
        }
        return ok;
    }

    private HashedFile open(PathGuard guard, String dir, String name) throws IOException {
        HashedFile f = new HashedFile(guard, dir, name);
        files.add(f);
        return f;
    }
}
