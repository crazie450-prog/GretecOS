package io.github.crazie450prog.gregteclink.export;

import java.io.IOException;
import java.lang.reflect.InvocationTargetException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

import cpw.mods.fml.common.FMLCommonHandler;
import cpw.mods.fml.common.Loader;
import io.github.crazie450prog.gregteclink.GregtecLink;

/**
 * The crop step (WO-1.10, plan §7.17): CropsNH's own NEI data dumpers, run right after NEI's recipe dump on the client
 * thread, as if each "Dump" button under NEI's Tools, Data Dumps, Crops NH had been clicked. CropsNH registers them in
 * NEI's option list as {@code tools.dump.cropsnh.<name>}; each {@code DataDumper.dumpFile()} writes
 * {@code <gameDir>/dumps/<name>.<ext>} and catches its own exceptions, logging {@code Error dumping ...} on NEI's
 * logger. So, as for NEI's step (D100 (1)), success is judged from that log and the files: the run keeps a
 * byte-for-byte
 * copy of each file in {@code crops/}. CropsNH is reached by reflection only, through NEI's API.
 */
final class CropTrigger {

    static final String MOD_ID = "cropsnh";
    /** CropsNH's dumpers, in NEI's option list. */
    static final String OPTIONS = "tools.dump." + MOD_ID;
    static final String DUMP_DIR = "dumps";
    /** The dumpers Python reads (crops, breeding recipes, mutation pools, soils, alternate seeds). */
    static final List<String> REQUIRED = Collections.unmodifiableList(
        Arrays.asList("alternateSeed", "cropRegistry", "deterministicMutations", "mutationPools", "soilRegistry"));
    /** What NEI's {@code DataDumper.dumpFile()} logs at ERROR when a dump fails. */
    static final String DUMP_FAILED = "Error dumping ";

    private CropTrigger() {}

    /** Whether CropsNH is in the game. */
    static boolean present() {
        try {
            return Loader.isModLoaded(MOD_ID);
        } catch (Throwable t) {
            return false;
        }
    }

    /**
     * {@code parts.crops} for a run whose crop step did not run: "absent" without CropsNH, "skipped_server" on a
     * dedicated server, else "failed" (the step runs only after NEI's recipe dump), with the reason recorded.
     */
    static String notRun(ExportRun run) {
        if (!present()) return "absent";
        boolean client;
        try {
            client = FMLCommonHandler.instance()
                .getSide()
                .isClient();
        } catch (Throwable t) {
            client = false;
        }
        if (!client) return "skipped_server";
        String nei = run.parts.get("nei");
        run.partErrors
            .put("crops", "not run: it runs right after NEI's recipe dump, which did not run (parts.nei=" + nei + ")");
        return "failed";
    }

    /**
     * Runs every CropsNH dumper on the calling (client) thread, then copies the files they wrote into the run's
     * {@code crops/}. Records counts, durations and any error on the run. Returns "absent" without CropsNH; "ok" only
     * if every {@link #REQUIRED} dumper is registered and, for each of them, the call threw nothing, NEI logged no
     * {@code Error dumping} line during it, it wrote its file and the copy succeeded; otherwise "failed". One of the
     * other dumpers failing only adds {@code part_warnings.crops}. Never throws.
     */
    static String dump(ExportRun run) {
        if (!present()) return "absent";
        try {
            return dumpOrThrow(run);
        } catch (Throwable t) {
            GregtecLink.LOG.error("export: crop step failed", t);
            return failed(run, Json.error(t));
        }
    }

    private static String dumpOrThrow(ExportRun run) throws Exception {
        PathGuard dumps;
        try {
            dumps = new PathGuard(run.gameDir.toPath(), DUMP_DIR);
        } catch (SecurityException | IOException e) {
            return failed(run, DUMP_DIR + " refused: " + Json.error(e));
        }
        Map<String, Object> dumpers;
        try {
            dumpers = dumpers();
        } catch (Throwable t) {
            return failed(run, "CropsNH's dumpers not found in NEI's option list: " + Json.error(t));
        }
        run.counts.put("crop_dumpers", dumpers.size());
        Outcome o = runAll(dumps, dumpers, NEI_LOG, p -> copy(run, p));
        if (o.dumpMs >= 0) run.durations.put("crops_dump", o.dumpMs);
        if (o.copyMs >= 0) {
            run.durations.put("crops_copy", o.copyMs);
            run.counts.put("crop_files", o.files);
        }
        String error = o.error();
        if (error != null) return failed(run, error);
        String warning = o.warning();
        if (warning != null) {
            GregtecLink.LOG.warn("export {}: crop step ok, but {}", run.runId, warning);
            run.partWarnings.put("crops", warning);
        }
        GregtecLink.LOG.info("export {}: CropsNH's {} dumpers wrote {} files", run.runId, dumpers.size(), o.files);
        return "ok";
    }

    /** Attaches a sink to NEI's log for one dumper call; returns what detaches it. */
    interface LogTap {

        Runnable attach(NeiLogCapture.Sink sink) throws ReflectiveOperationException;
    }

    /** NEI's own logger ({@link NeiLogCapture}). */
    static final LogTap NEI_LOG = sink -> NeiLogCapture.attach(sink)::detach;

    /** Copies one written dump file into the run's {@code crops/}. */
    interface Copier {

        void copy(Path file) throws IOException, InterruptedException;
    }

    /** What {@link #runAll} found; the run's status follows from {@link #error} and {@link #warning}. */
    static final class Outcome {

        /** The step can't go on at all (a required dumper unregistered, NEI's API unreachable), or null. */
        String fatal;
        /** The required dumpers that failed, as "name: why". */
        final List<String> errors = new ArrayList<>();
        /** The other dumpers that failed. */
        final List<String> others = new ArrayList<>();
        /** The dumpers whose file was copied, sorted. */
        final List<String> copied = new ArrayList<>();
        int files;
        long dumpMs = -1;
        long copyMs = -1;

        /** {@code part_errors.crops}, or null if the step is ok. */
        String error() {
            if (fatal != null) return fatal;
            return errors.isEmpty() ? null : errors.size() + " dumper(s) failed, e.g. " + errors.get(0);
        }

        /** {@code part_warnings.crops} of an ok step, or null. */
        String warning() {
            if (error() != null || others.isEmpty()) return null;
            return others.size() + " of CropsNH's other dumpers failed (their files are missing from crops/), e.g. "
                + others.get(0);
        }
    }

    /**
     * Runs every dumper (by short name, sorted) with {@code log} tapped during each call, then copies the files they
     * wrote. A {@link #REQUIRED} dumper's failure fails the step; one of the others' is only a warning. Never throws.
     */
    static Outcome runAll(PathGuard dumps, Map<String, Object> dumpers, LogTap log, Copier copier) {
        Outcome o = new Outcome();
        long t0 = System.currentTimeMillis();
        List<String> missing = new ArrayList<>(REQUIRED);
        missing.removeAll(dumpers.keySet());
        if (!missing.isEmpty()) {
            o.fatal = "CropsNH registered no dumper " + missing + " under " + OPTIONS + ": " + dumpers.keySet();
            return o;
        }
        Map<String, Path> written = new TreeMap<>();
        for (Map.Entry<String, Object> e : dumpers.entrySet()) {
            String why;
            try {
                why = runOne(dumps, e.getKey(), e.getValue(), log, written);
            } catch (ReflectiveOperationException ex) {
                o.fatal = "NEI's dumper API or log not reachable: " + Json.error(ex);
                return o;
            } catch (IOException | RuntimeException ex) { // a path refused or unreadable
                why = Json.error(ex);
            }
            if (why != null) (REQUIRED.contains(e.getKey()) ? o.errors : o.others).add(e.getKey() + ": " + why);
        }
        o.dumpMs = System.currentTimeMillis() - t0;

        long t = System.currentTimeMillis();
        for (Map.Entry<String, Path> e : written.entrySet()) {
            try {
                copier.copy(e.getValue());
                o.files++;
                o.copied.add(e.getKey());
            } catch (Throwable ex) {
                if (ex instanceof InterruptedException) Thread.currentThread()
                    .interrupt();
                (REQUIRED.contains(e.getKey()) ? o.errors : o.others)
                    .add(e.getKey() + ": copy failed: " + Json.error(ex));
            }
        }
        o.copyMs = System.currentTimeMillis() - t;
        return o;
    }

    /**
     * Runs one dumper, with NEI's log tapped during the call; adds its file to {@code written}. Returns why it failed,
     * or null. Throws only if NEI's API or logger can't be reached.
     */
    private static String runOne(PathGuard dumps, String name, Object dumper, LogTap tap, Map<String, Path> written)
        throws ReflectiveOperationException, IOException {
        String file = (String) dumper.getClass()
            .getMethod("getFileName", String.class)
            .invoke(dumper, name);
        Path target = dumps.resolve(file);
        if (!target.getParent()
            .equals(dumps.root())) return "file " + file + " is not directly in " + DUMP_DIR;
        // The dumper writes through the file: refuse one that is a link or not a regular file.
        if (Files.exists(target, LinkOption.NOFOLLOW_LINKS) && !Files.isRegularFile(target, LinkOption.NOFOLLOW_LINKS))
            return DUMP_DIR + "/" + file + " refused: not a regular file";
        NeiLogCapture.Lines log = new NeiLogCapture.Lines(DUMP_FAILED);
        Runnable detach = tap.attach(log);
        long start = System.currentTimeMillis();
        try {
            dumper.getClass()
                .getMethod("dumpFile")
                .invoke(dumper);
        } catch (InvocationTargetException ex) {
            return "dumpFile threw " + Json.error(ex.getCause());
        } finally {
            detach.run();
        }
        if (log.count() > 0) return "NEI logged '" + log.first() + "'";
        if (!Files.isRegularFile(target, LinkOption.NOFOLLOW_LINKS)
            || Files.getLastModifiedTime(target, LinkOption.NOFOLLOW_LINKS)
                .toMillis() < start - NeiTrigger.MTIME_SLACK_MS)
            return "wrote no " + DUMP_DIR + "/" + file;
        // CropsNH's PrintWriter swallows write errors (a full disk): an empty file is the one sign left, as every
        // dump has at least a header. A file cut short later is not detected (D134).
        if (Files.size(target) == 0) return "wrote an empty " + DUMP_DIR + "/" + file;
        written.put(name, target);
        return null;
    }

    /**
     * CropsNH's dumpers by their short name (the part after {@code tools.dump.cropsnh.}, which NEI also uses for the
     * file name), sorted: every {@code DataDumper} in NEI's {@code tools.dump.cropsnh} option list.
     */
    private static Map<String, Object> dumpers() throws ReflectiveOperationException {
        Map<String, Object> out = new TreeMap<>();
        Class<?> dataDumper = Class.forName("codechicken.nei.config.DataDumper");
        Class<?> optionList = Class.forName("codechicken.nei.config.OptionList");
        Object root = Class.forName("codechicken.nei.NEIClientConfig")
            .getMethod("getOptionList")
            .invoke(null);
        Object list = optionList.getMethod("getOption", String.class)
            .invoke(root, OPTIONS);
        if (!optionList.isInstance(list)) return out;
        for (Object o : (List<?>) optionList.getField("optionList")
            .get(list)) {
            if (!dataDumper.isInstance(o)) continue;
            String name = (String) o.getClass()
                .getField("name")
                .get(o);
            out.put(name.substring(name.lastIndexOf('.') + 1), o);
        }
        return out;
    }

    private static void copy(ExportRun run, Path p) throws IOException, InterruptedException {
        for (int attempt = 1;; attempt++) {
            try {
                run.addCopiedFile("crops/" + p.getFileName(), p);
                return;
            } catch (IOException e) {
                if (attempt >= NeiTrigger.COPY_ATTEMPTS) throw e;
                GregtecLink.LOG.warn("copy of {} failed (try {}): {}; retrying", p, attempt, e.toString());
                Thread.sleep(1000L << (attempt - 1));
            }
        }
    }

    private static String failed(ExportRun run, String reason) {
        GregtecLink.LOG.error("export {}: crop step failed: {}", run.runId, reason);
        run.partErrors.put("crops", reason);
        return "failed";
    }
}
