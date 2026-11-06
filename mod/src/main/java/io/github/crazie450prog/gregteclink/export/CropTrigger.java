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
     * if every required dumper is registered, no call threw, NEI logged no {@code Error dumping} line, every dumper
     * wrote its file and every copy succeeded; otherwise "failed". Never throws.
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
        long t0 = System.currentTimeMillis();
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
        List<String> missing = new ArrayList<>(REQUIRED);
        missing.removeAll(dumpers.keySet());
        if (!missing.isEmpty()) {
            return failed(
                run,
                "CropsNH registered no dumper " + missing + " under " + OPTIONS + ": " + dumpers.keySet());
        }

        List<String> errors = new ArrayList<>();
        List<Path> written = new ArrayList<>();
        NeiLogCapture.Lines log;
        NeiLogCapture capture;
        try {
            log = new NeiLogCapture.Lines(DUMP_FAILED);
            capture = NeiLogCapture.attach(log);
        } catch (Throwable t) {
            return failed(run, "NEI's log can't be captured: " + Json.error(t));
        }
        try {
            for (Map.Entry<String, Object> e : dumpers.entrySet()) {
                Object dumper = e.getValue();
                String file = (String) dumper.getClass()
                    .getMethod("getFileName", String.class)
                    .invoke(dumper, e.getKey());
                Path target = dumps.resolve(file);
                if (!target.getParent()
                    .equals(dumps.root())) {
                    errors.add(e.getKey() + ": file " + file + " is not directly in " + DUMP_DIR);
                    continue;
                }
                // The dumper writes through the file: refuse one that is a link or not a regular file.
                if (Files.exists(target, LinkOption.NOFOLLOW_LINKS)
                    && !Files.isRegularFile(target, LinkOption.NOFOLLOW_LINKS)) {
                    errors.add(e.getKey() + ": " + DUMP_DIR + "/" + file + " refused: not a regular file");
                    continue;
                }
                long start = System.currentTimeMillis();
                try {
                    dumper.getClass()
                        .getMethod("dumpFile")
                        .invoke(dumper);
                } catch (InvocationTargetException ex) {
                    errors.add(e.getKey() + ": dumpFile threw " + Json.error(ex.getCause()));
                    continue;
                }
                if (!Files.isRegularFile(target, LinkOption.NOFOLLOW_LINKS)
                    || Files.getLastModifiedTime(target, LinkOption.NOFOLLOW_LINKS)
                        .toMillis() < start - NeiTrigger.MTIME_SLACK_MS) {
                    errors.add(e.getKey() + ": wrote no " + DUMP_DIR + "/" + file);
                    continue;
                }
                written.add(target);
            }
        } finally {
            capture.detach();
        }
        run.durations.put("crops_dump", System.currentTimeMillis() - t0);

        long t = System.currentTimeMillis();
        int files = 0;
        for (Path p : written) {
            try {
                copy(run, p);
                files++;
            } catch (Throwable e) {
                if (e instanceof InterruptedException) Thread.currentThread()
                    .interrupt();
                errors.add(p.getFileName() + ": copy failed: " + Json.error(e));
            }
        }
        run.durations.put("crops_copy", System.currentTimeMillis() - t);
        run.counts.put("crop_files", files);

        String reason = null;
        if (log.count() > 0) reason = "NEI logged '" + DUMP_FAILED + "...': " + log.first();
        else if (!errors.isEmpty()) reason = errors.size() + " dumper(s) failed, e.g. " + errors.get(0);
        if (reason != null) return failed(run, reason);
        GregtecLink.LOG.info("export {}: CropsNH's {} dumpers wrote {} files", run.runId, dumpers.size(), files);
        return "ok";
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
                if (attempt >= 3) throw e;
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
