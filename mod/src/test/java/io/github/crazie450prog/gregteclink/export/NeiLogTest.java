package io.github.crazie450prog.gregteclink.export;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

/** The NEI 2.8.172 log lines the NEI step judges a dump by (RecipeJsonWriter, RecipeHandlerQuery). */
class NeiLogTest {

    @Test
    void aGoodDump() {
        NeiLog log = new NeiLog();
        log.add("INFO", "FindFuels took 138.1 ms", null);
        log.add("INFO", "Loading 182 recipe handlers took 2.009 s", null);
        log.add("WARN", "Skipping recipe 12 of Assembler", "java.lang.NullPointerException");
        log.add("INFO", "Writing 139869 recipes took 9.178 s", null);
        assertEquals(182, log.handlers);
        assertEquals(139869, log.reported);
        assertEquals(1, log.skipped);
        assertEquals(0, log.failed);
        assertEquals(0, log.lookupErrors);
        assertNull(log.firstError);
        assertNull(log.warning());
    }

    /** Seen with dumps/recipes made a file: NEI returns normally and only logs this. */
    @Test
    void aFailedDump() {
        NeiLog log = new NeiLog();
        log.add("INFO", "Loading 182 recipe handlers took 796.9 ms", null);
        log.add("ERROR", "Error dumping recipes", "java.lang.NullPointerException");
        assertEquals(-1, log.reported);
        assertEquals(1, log.failed);
        assertEquals("java.lang.NullPointerException", log.firstError);
    }

    @Test
    void handlerLookupErrorsAreCounted() {
        NeiLog log = new NeiLog();
        log.add("ERROR", "Error while looking up crafting recipe", null);
        log.add("ERROR", "outputId: all", null);
        log.add("ERROR", "results: []", null);
        assertEquals(1, log.lookupErrors);
        assertEquals(0, log.failed);
        assertTrue(
            log.warning()
                .startsWith("1 recipe handler(s) failed to load and are missing from nei/"),
            log.warning());
    }

    @Test
    void otherLevelsAndMessagesAreIgnored() {
        NeiLog log = new NeiLog();
        log.add("DEBUG", "Writing 5 recipes took 1 ms", null);
        log.add("INFO", "Writing many recipes took 1 ms", null);
        log.add("WARN", "Error dumping recipes", null);
        log.add("INFO", null, null);
        assertEquals(-1, log.reported);
        assertEquals(0, log.failed);
    }

    /** The crop step (WO-1.10): NEI's DataDumper logs "Error dumping <name> mode: <n>" and returns normally. */
    @Test
    void cropDumperErrorsAreCaught() {
        NeiLogCapture.Lines lines = new NeiLogCapture.Lines(CropTrigger.DUMP_FAILED);
        lines.add("INFO", "Error dumping Crops mode: 0", null);
        lines.add("ERROR", "Registering NEI dumpers", null);
        lines.add("ERROR", null, null);
        assertEquals(0, lines.count());
        assertNull(lines.first());
        lines.add("ERROR", "Error dumping Crops mode: 0", "java.io.FileNotFoundException: dumps/cropRegistry.csv");
        lines.add("ERROR", "Error dumping Soils mode: 0", null);
        assertEquals(2, lines.count());
        assertEquals(
            "Error dumping Crops mode: 0: java.io.FileNotFoundException: dumps/cropRegistry.csv",
            lines.first());
    }
}
