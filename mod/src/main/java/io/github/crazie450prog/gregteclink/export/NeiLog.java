package io.github.crazie450prog.gregteclink.export;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * What NEI 2.8.172's {@code RecipeJsonWriter.dumpAll} logged during one call. It returns normally even when it fails
 * (it catches every exception), so its log lines on the {@code NotEnoughItems} logger are the only signal:
 * <ul>
 * <li>INFO {@code Loading N recipe handlers took ...}</li>
 * <li>INFO {@code Writing N recipes took ...}: the lines it wrote, in every file together</li>
 * <li>ERROR {@code Error dumping recipes}: the dump failed and stopped</li>
 * <li>WARN {@code Skipping recipe I of HANDLER}: one recipe failed and was left out</li>
 * <li>ERROR {@code Error while looking up crafting recipe}: a handler failed to load and was dropped without
 * notice (its stack trace goes to stderr, not to this logger)</li>
 * </ul>
 */
final class NeiLog implements NeiLogCapture.Sink {

    private static final Pattern HANDLERS = Pattern.compile("Loading (\\d+) recipe handlers took .*");
    private static final Pattern WRITTEN = Pattern.compile("Writing (\\d+) recipes took .*");
    static final String DUMP_FAILED = "Error dumping recipes";
    static final String SKIPPED = "Skipping recipe ";
    static final String LOOKUP_ERROR = "Error while looking up crafting recipe";

    /** From "Loading N recipe handlers", or -1 if NEI logged none. */
    long handlers = -1;
    /** From "Writing N recipes", or -1 if NEI logged none. */
    long reported = -1;
    int failed;
    int skipped;
    int lookupErrors;
    /** The first failure's throwable, as "class: message"; null if none was logged. */
    String firstError;

    /**
     * The manifest's {@code part_warnings.nei} for a dump that is otherwise ok: null unless a handler was dropped,
     * since the lines then still equal NEI's count but that handler's recipes are missing.
     */
    String warning() {
        if (lookupErrors == 0) return null;
        return lookupErrors + " recipe handler(s) failed to load and are missing from nei/ (NEI logged '"
            + LOOKUP_ERROR
            + "'; the stack traces are the game log's STDERR lines)";
    }

    /** One captured event: its level name, formatted message, and throwable summary (or null). */
    @Override
    public void add(String level, String message, String thrown) {
        if (message == null) return;
        Matcher m;
        if ("INFO".equals(level) && (m = HANDLERS.matcher(message)).matches()) {
            handlers = Long.parseLong(m.group(1));
        } else if ("INFO".equals(level) && (m = WRITTEN.matcher(message)).matches()) {
            reported = Math.max(reported, 0) + Long.parseLong(m.group(1));
        } else if ("ERROR".equals(level) && message.equals(DUMP_FAILED)) {
            failed++;
            if (firstError == null) firstError = thrown == null ? message : thrown;
        } else if ("WARN".equals(level) && message.startsWith(SKIPPED)) {
            skipped++;
        } else if ("ERROR".equals(level) && message.equals(LOOKUP_ERROR)) {
            lookupErrors++;
        }
    }
}
