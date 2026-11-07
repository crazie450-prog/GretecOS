package io.github.crazie450prog.gregteclink.export;

import java.io.IOException;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

import io.github.crazie450prog.gregteclink.GregtecLink;

/**
 * (c) of plan §5.2 step 2: NEI 2.8.172's own recipe export, {@code RecipeJsonWriter.dumpAll(true)} (the same files as
 * Data Dumps, All Handler Recipes, Separate), called by reflection on the client thread once NEI has loaded. NEI
 * writes {@code <gameDir>/dumps/recipes/<handler name>.jsonl}; the run keeps a copy of each file in {@code nei/}.
 */
final class NeiTrigger {

    static final String WRITER_CLASS = "codechicken.nei.recipe.RecipeJsonWriter";
    static final String DUMP_DIR = "dumps/recipes";
    /** A file NEI wrote during the call has an mtime no older than the call's start minus this (FAT: 2 s steps). */
    static final long MTIME_SLACK_MS = 2000;
    /** Each copy is tried 3 times, as Windows may still hold a just-written file. */
    static final int COPY_ATTEMPTS = 3;

    private static Method isLoaded;
    private static Field loadFinished;

    private NeiTrigger() {}

    /** Whether NEI ≥ 2.8.172's writer is in the game. */
    static boolean writerPresent() {
        try {
            Class.forName(WRITER_CLASS, false, NeiTrigger.class.getClassLoader());
            return true;
        } catch (Throwable t) {
            return false;
        }
    }

    /** The status of a run that has no NEI step: "absent" without the writer, else "skipped_server". */
    static String serverStatus() {
        return writerPresent() ? "skipped_server" : "absent";
    }

    /**
     * NEI has registered its handlers ({@code NEIClientConfig.isLoaded()}) and loaded its item list
     * ({@code ItemList.loadFinished}). False if either can't be read.
     */
    static boolean ready() {
        try {
            if (isLoaded == null) {
                isLoaded = Class.forName("codechicken.nei.NEIClientConfig")
                    .getMethod("isLoaded");
                loadFinished = Class.forName("codechicken.nei.ItemList")
                    .getField("loadFinished");
            }
            return (Boolean) isLoaded.invoke(null) && loadFinished.getBoolean(null);
        } catch (Throwable t) {
            return false;
        }
    }

    /**
     * Runs NEI's dump on the calling (client) thread, which freezes the game until it returns, with every recipe
     * handler loaded on that thread ({@link NeiSerialLookup}, D188), then copies the files it wrote into the run.
     * Records counts, durations and any error on the run. Returns "ok" only if the call threw nothing, NEI logged no
     * "Error dumping recipes", at least one file was found, every copy succeeded and the lines copied equal the count
     * NEI logged; otherwise "failed". An ok step whose NEI dropped a handler also gets {@code part_warnings.nei}.
     * Never throws.
     */
    static String dump(ExportRun run) {
        try {
            return dumpOrThrow(run);
        } catch (Throwable t) {
            GregtecLink.LOG.error("export: NEI step failed", t);
            return failed(run, Json.error(t));
        }
    }

    private static String dumpOrThrow(ExportRun run) throws IllegalAccessException {
        // NEI deletes every file in dumps/recipes first: refuse a link or junction at dumps or dumps/recipes.
        PathGuard dumps;
        try {
            dumps = new PathGuard(run.gameDir.toPath(), DUMP_DIR);
        } catch (SecurityException | IOException e) {
            return failed(run, DUMP_DIR + " refused: " + Json.error(e));
        }
        Method dumpAll;
        try {
            dumpAll = Class.forName(WRITER_CLASS)
                .getMethod("dumpAll", boolean.class);
        } catch (Throwable t) {
            return failed(run, "NEI's writer not callable: " + Json.error(t));
        }
        NeiLog log = new NeiLog();
        NeiLogCapture capture;
        try {
            capture = NeiLogCapture.attach(log);
        } catch (Throwable t) {
            return failed(run, "NEI's log can't be captured: " + Json.error(t));
        }
        NeiSerialLookup serial;
        try {
            serial = NeiSerialLookup.begin();
        } catch (Throwable t) {
            capture.detach();
            return failed(run, "NEI's handler lookup can't be made serial: " + Json.error(t));
        }
        long start = System.currentTimeMillis();
        Throwable thrown = null;
        try {
            GregtecLink.LOG.info(
                "export {}: NEI dumpAll(true) on {}, all {} recipe handlers loaded serially on it",
                run.runId,
                Thread.currentThread()
                    .getName(),
                serial.handlers);
            dumpAll.invoke(null, true);
        } catch (InvocationTargetException e) {
            thrown = e.getCause();
        } catch (Throwable t) {
            thrown = t;
        } finally {
            capture.detach();
            serial.restore();
        }
        run.durations.put("nei_dump", System.currentTimeMillis() - start);
        run.counts.put("nei_handlers", log.handlers);
        run.counts.put("nei_reported", log.reported);
        run.counts.put("nei_skipped", log.skipped);
        run.counts.put("nei_lookup_errors", log.lookupErrors);

        long t = System.currentTimeMillis();
        List<String> copyErrors = new ArrayList<>();
        List<Path> found = new ArrayList<>();
        try {
            found = written(dumps.root(), start - MTIME_SLACK_MS);
        } catch (IOException e) {
            copyErrors.add(DUMP_DIR + " not listed: " + Json.error(e));
        }
        int files = 0;
        long lines = 0;
        for (Path p : found) {
            try {
                lines += copy(run, p).lines();
                files++;
            } catch (Throwable e) {
                if (e instanceof InterruptedException) Thread.currentThread()
                    .interrupt();
                copyErrors.add(p.getFileName() + ": " + Json.error(e));
            }
        }
        run.durations.put("nei_copy", System.currentTimeMillis() - t);
        run.counts.put("nei_files", files);
        run.counts.put("nei_recipes", lines);

        String reason = null;
        if (thrown != null) reason = "dumpAll threw " + Json.error(thrown);
        else if (log.failed > 0) reason = "NEI logged '" + NeiLog.DUMP_FAILED + "': " + log.firstError;
        else if (files == 0 && copyErrors.isEmpty()) reason = "NEI wrote no file in " + DUMP_DIR;
        else if (!copyErrors.isEmpty()) reason = copyErrors.size() + " copies failed, e.g. " + copyErrors.get(0);
        else if (log.reported < 0) reason = "NEI's 'Writing N recipes' line was not captured";
        else if (lines != log.reported) reason = lines + " lines copied, NEI reported " + log.reported;
        if (reason != null) return failed(run, reason);
        String warning = log.warning();
        if (warning != null) {
            GregtecLink.LOG.warn("export {}: NEI step ok, but {}", run.runId, warning);
            run.partWarnings.put("nei", warning);
        }
        GregtecLink.LOG.info(
            "export {}: NEI wrote {} recipes in {} files ({} skipped, {} handler lookup errors)",
            run.runId,
            lines,
            files,
            log.skipped,
            log.lookupErrors);
        return "ok";
    }

    /**
     * The {@code .jsonl} files directly in {@code dir} modified at or after {@code since}, sorted by name: regular
     * files only, never a link. Older files are ones NEI could not delete and did not rewrite.
     */
    static List<Path> written(Path dir, long since) throws IOException {
        List<Path> out = new ArrayList<>();
        try (DirectoryStream<Path> s = Files.newDirectoryStream(dir, "*.jsonl")) {
            for (Path p : s) {
                if (Files.isRegularFile(p, LinkOption.NOFOLLOW_LINKS)
                    && Files.getLastModifiedTime(p, LinkOption.NOFOLLOW_LINKS)
                        .toMillis() >= since)
                    out.add(p);
            }
        }
        Collections.sort(out);
        return out;
    }

    private static HashedFile copy(ExportRun run, Path p) throws IOException, InterruptedException {
        for (int attempt = 1;; attempt++) {
            try {
                return run.addNeiFile(p);
            } catch (IOException e) {
                if (attempt >= COPY_ATTEMPTS) throw e;
                GregtecLink.LOG.warn("copy of {} failed (try {}): {}; retrying", p, attempt, e.toString());
                Thread.sleep(1000L << (attempt - 1));
            }
        }
    }

    private static String failed(ExportRun run, String reason) {
        GregtecLink.LOG.error("export {}: NEI step failed: {}", run.runId, reason);
        run.partErrors.put("nei", reason);
        return "failed";
    }
}
