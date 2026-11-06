package io.github.crazie450prog.gregteclink.export;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * The crop step's rules (WO-1.10) with fake dumpers in a temp folder: which failures fail the step and which only
 * warn, and which files are refused or not copied. The client harness covers the real dumpers' success path.
 */
class CropTriggerTest {

    /** The dumper the tests break, one of {@link CropTrigger#REQUIRED}. */
    private static final String CROPS = "cropRegistry";
    /** One of CropsNH's other dumpers. */
    private static final String OTHER = "fertilizerItems";

    @TempDir
    Path tmp;

    private PathGuard dumps;
    private Tap tap;
    private Map<String, Object> dumpers;
    private final List<Path> copied = new ArrayList<>();

    @BeforeEach
    void setUp() throws IOException {
        dumps = new PathGuard(tmp, CropTrigger.DUMP_DIR);
        tap = new Tap();
        dumpers = new TreeMap<>();
        for (String name : CropTrigger.REQUIRED) put(name, "ok");
        put(OTHER, "ok");
    }

    @Test
    void everyDumperOk() {
        CropTrigger.Outcome o = run();
        assertNull(o.error());
        assertNull(o.warning());
        assertEquals(6, o.files);
        assertEquals(new ArrayList<>(dumpers.keySet()), o.copied);
        assertEquals(6, copied.size());
        assertEquals(6, tap.attached);
        assertEquals(6, tap.detached);
        assertTrue(o.dumpMs >= 0 && o.copyMs >= 0);
    }

    @Test
    void aRequiredDumperUnregisteredFailsBeforeAnyRuns() {
        dumpers.remove("soilRegistry");
        CropTrigger.Outcome o = run();
        assertTrue(
            o.error()
                .startsWith("CropsNH registered no dumper [soilRegistry] under tools.dump.cropsnh"),
            o.error());
        assertEquals(0, tap.attached);
        assertTrue(copied.isEmpty());
        assertEquals(-1, o.dumpMs);
    }

    @Test
    void aRequiredDumperFailingFailsTheStep() throws IOException {
        String[][] cases = { { "logs", "NEI logged 'Error dumping Crops mode: 0: java.io.IOException: boom'" },
            { "throws", "dumpFile threw java.lang.IllegalStateException: boom" },
            { "nothing", "wrote no dumps/cropRegistry.csv" }, { "empty", "wrote an empty dumps/cropRegistry.csv" } };
        for (String[] c : cases) {
            Files.deleteIfExists(
                dumps.root()
                    .resolve(CROPS + ".csv"));
            copied.clear();
            put(CROPS, c[0]);
            CropTrigger.Outcome o = run();
            assertEquals("1 dumper(s) failed, e.g. " + CROPS + ": " + c[1], o.error(), c[0]);
            assertNull(o.warning(), c[0]);
            assertEquals(5, o.files, c[0]);
            assertTrue(!o.copied.contains(CROPS), c[0]);
        }
        assertEquals(tap.attached, tap.detached);
    }

    @Test
    void anotherDumperFailingIsOnlyAWarning() {
        put(OTHER, "logs");
        CropTrigger.Outcome o = run();
        assertNull(o.error());
        assertEquals(
            "1 of CropsNH's other dumpers failed (their files are missing from crops/), e.g. " + OTHER
                + ": NEI logged 'Error dumping Crops mode: 0: java.io.IOException: boom'",
            o.warning());
        assertEquals(5, o.files);
    }

    /** A file left by an earlier run is not this call's dump. */
    @Test
    void aStaleFileIsNotADump() throws IOException {
        Path old = Files.write(
            dumps.root()
                .resolve(CROPS + ".csv"),
            "ID,Name\n".getBytes(StandardCharsets.UTF_8));
        Files.setLastModifiedTime(old, FileTime.fromMillis(System.currentTimeMillis() - 60_000));
        put(CROPS, "nothing");
        assertEquals("1 dumper(s) failed, e.g. " + CROPS + ": wrote no dumps/cropRegistry.csv", run().error());
        assertTrue(!copied.contains(old));
    }

    @Test
    void aFolderAtTheTargetIsRefusedAndTheDumperNotCalled() throws IOException {
        Files.createDirectory(
            dumps.root()
                .resolve(CROPS + ".csv"));
        put(CROPS, "throws");
        assertEquals(
            "1 dumper(s) failed, e.g. " + CROPS + ": dumps/cropRegistry.csv refused: not a regular file",
            run().error());
        assertEquals(5, tap.attached);
    }

    /** Even to a file in the dump folder (one outside it is refused by the path guard already). */
    @Test
    void aLinkAtTheTargetIsRefused() throws IOException {
        Path real = Files.write(
            dumps.root()
                .resolve("real.txt"),
            new byte[] { 'x' });
        try {
            Files.createSymbolicLink(
                dumps.root()
                    .resolve(CROPS + ".csv"),
                real);
        } catch (UnsupportedOperationException | IOException e) {
            assumeTrue(false, "no symlinks here");
        }
        put(CROPS, "ok");
        assertEquals(
            "1 dumper(s) failed, e.g. " + CROPS + ": dumps/cropRegistry.csv refused: not a regular file",
            run().error());
        assertEquals("x", new String(Files.readAllBytes(real), StandardCharsets.UTF_8));
    }

    @Test
    void aFileOutsideTheDumpFolderIsRefused() {
        dumpers.put(CROPS, new FakeDumper(dumps.root(), "sub/x.csv", "ok", tap));
        assertEquals("1 dumper(s) failed, e.g. " + CROPS + ": file sub/x.csv is not directly in dumps", run().error());
        dumpers.put(CROPS, new FakeDumper(dumps.root(), "../x.csv", "ok", tap));
        String error = run().error();
        assertTrue(error.startsWith("1 dumper(s) failed, e.g. " + CROPS + ": java.lang.SecurityException"), error);
        assertTrue(Files.notExists(tmp.resolve("x.csv")));
    }

    @Test
    void aFailedCopyFailsARequiredDumperAndWarnsForAnother() {
        CropTrigger.Outcome o = CropTrigger.runAll(dumps, dumpers, tap, p -> {
            if (p.getFileName()
                .toString()
                .startsWith("mutationPools")) throw new IOException("disk full");
            copied.add(p);
        });
        assertEquals("1 dumper(s) failed, e.g. mutationPools: copy failed: java.io.IOException: disk full", o.error());
        assertEquals(5, o.files);

        o = CropTrigger.runAll(
            dumps,
            dumpers,
            tap,
            p -> {
                if (p.getFileName()
                    .toString()
                    .startsWith(OTHER)) throw new IOException("disk full");
            });
        assertNull(o.error());
        assertTrue(
            o.warning()
                .endsWith(OTHER + ": copy failed: java.io.IOException: disk full"),
            o.warning());
    }

    @Test
    void neisApiOrLogNotReachableIsFatal() {
        dumpers.put(CROPS, new Object()); // no getFileName
        String error = run().error();
        assertTrue(error.startsWith("NEI's dumper API or log not reachable: java.lang.NoSuchMethodException"), error);

        put(CROPS, "ok");
        CropTrigger.Outcome o = CropTrigger.runAll(
            dumps,
            dumpers,
            sink -> { throw new ClassNotFoundException("codechicken.nei.NEIClientConfig"); },
            copied::add);
        assertEquals(
            "NEI's dumper API or log not reachable: java.lang.ClassNotFoundException: codechicken.nei.NEIClientConfig",
            o.error());
        assertNotNull(o.fatal);
        assertEquals(-1, o.copyMs);
    }

    private CropTrigger.Outcome run() {
        return CropTrigger.runAll(dumps, dumpers, tap, copied::add);
    }

    private void put(String name, String mode) {
        dumpers.put(name, new FakeDumper(dumps.root(), name + ".csv", mode, tap));
    }

    /** NEI's log for the tests: the sink attached during each call. */
    static final class Tap implements CropTrigger.LogTap {

        NeiLogCapture.Sink sink;
        int attached;
        int detached;

        @Override
        public Runnable attach(NeiLogCapture.Sink s) {
            sink = s;
            attached++;
            return () -> {
                detached++;
                sink = null;
            };
        }
    }

    /** NEI's {@code DataDumper} as the step calls it: {@code getFileName(String)} and {@code dumpFile()}. */
    public static final class FakeDumper {

        private final Path dir;
        private final String file;
        private final String mode;
        private final Tap tap;

        FakeDumper(Path dir, String file, String mode, Tap tap) {
            this.dir = dir;
            this.file = file;
            this.mode = mode;
            this.tap = tap;
        }

        public String getFileName(String name) {
            return file;
        }

        public void dumpFile() throws IOException {
            switch (mode) {
                case "ok":
                    Files.write(dir.resolve(file), Arrays.asList("ID,Name", "a,b"), StandardCharsets.UTF_8);
                    break;
                case "empty":
                    Files.write(dir.resolve(file), new byte[0]);
                    break;
                case "logs": // NEI's DataDumper catches the dump's exception and logs it
                    tap.sink.add("ERROR", "Error dumping Crops mode: 0", "java.io.IOException: boom");
                    break;
                case "throws":
                    throw new IllegalStateException("boom");
                default: // "nothing"
            }
        }
    }
}
