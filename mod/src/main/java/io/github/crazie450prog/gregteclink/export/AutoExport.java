package io.github.crazie450prog.gregteclink.export;

import io.github.crazie450prog.gregteclink.GregtecLink;

/**
 * (e) of plan §5.2 step 2. PR 1 stub: unattended export (load or create the void world, export, quit) needs a client
 * and V22, so it comes in PR 1b. Until then a requested autoexport is only reported.
 */
final class AutoExport {

    static final String PROPERTY = "gregtec.autoexport";

    private AutoExport() {}

    static boolean requested() {
        return Boolean.getBoolean(PROPERTY);
    }

    /** "off", or "not_built" when -Dgregtec.autoexport=true is set. */
    static String status() {
        if (!requested()) return "off";
        GregtecLink.LOG.warn("-D{}=true is set, but autoexport is not built yet (PR 1b, V22)", PROPERTY);
        return "not_built";
    }
}
