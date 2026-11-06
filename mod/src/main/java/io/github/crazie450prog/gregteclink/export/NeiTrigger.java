package io.github.crazie450prog.gregteclink.export;

/**
 * (c) of plan §5.2 step 2. PR 1 stub: NEI's {@code RecipeJsonWriter.dumpAll} is client-only (it needs NEI's item list
 * and handlers loaded), so the server-side export only reports whether it could run. PR 1b calls it on the client
 * thread after the GT parts finish (V22).
 */
final class NeiTrigger {

    static final String WRITER_CLASS = "codechicken.nei.recipe.RecipeJsonWriter";

    private NeiTrigger() {}

    /** "absent" without NEI ≥ 2.8.172's writer; otherwise "skipped_server" until PR 1b adds the client part. */
    static String status() {
        try {
            Class.forName(WRITER_CLASS, false, NeiTrigger.class.getClassLoader());
        } catch (Throwable t) {
            return "absent";
        }
        return "skipped_server";
    }
}
