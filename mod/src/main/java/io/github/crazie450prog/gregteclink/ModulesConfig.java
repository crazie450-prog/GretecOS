package io.github.crazie450prog.gregteclink;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Per-instance module switches, read from {@code config/gregteclink.cfg}.
 *
 * <p>
 * Every module defaults to off, and the file is only ever read: a jar without a config does nothing and writes
 * nothing. It is parsed here rather than with Forge's {@code Configuration}, which renames a malformed file to
 * {@code .errored} and creates a new one. Export's config ({@code export=true}) is in Export's {@code sync_exclude};
 * Play gets listener and client later through a script Clayton runs.
 */
public final class ModulesConfig {

    public static final String FILE_NAME = "gregteclink.cfg";
    public static final String CATEGORY = "modules";

    /** Forge's boolean line, {@code B:name=value} or {@code B:"name"=value}. */
    private static final Pattern BOOLEAN = Pattern.compile("B:(\"?)([^\"=]+)\\1=(.*)");

    public final boolean export;
    public final boolean listener;
    public final boolean client;

    private ModulesConfig(boolean export, boolean listener, boolean client) {
        this.export = export;
        this.listener = listener;
        this.client = client;
    }

    public static ModulesConfig load(File configDir) {
        File file = new File(configDir, FILE_NAME);
        if (!file.isFile()) {
            return new ModulesConfig(false, false, false);
        }
        try {
            return parse(new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8));
        } catch (Throwable t) {
            GregtecLink.LOG.warn("{} not read ({}): every module stays off", file, t.toString());
            return new ModulesConfig(false, false, false);
        }
    }

    /**
     * Forge's config syntax, as far as this file needs it: categories in braces, {@code #} comments, and the
     * {@code B:} lines of the top-level {@code modules} category, true or false in any case (as Forge reads them).
     * Other lines are ignored; unbalanced braces throw.
     */
    static ModulesConfig parse(String text) {
        boolean export = false;
        boolean listener = false;
        boolean client = false;
        Deque<String> categories = new ArrayDeque<>();
        for (String raw : text.split("\r\n|\r|\n")) {
            String line = raw.trim();
            if (line.isEmpty() || line.startsWith("#")) continue;
            if (line.endsWith("{")) {
                categories.push(
                    line.substring(0, line.length() - 1)
                        .trim()
                        .replace("\"", ""));
                continue;
            }
            if (line.equals("}")) {
                if (categories.isEmpty()) throw new IllegalArgumentException("unbalanced }");
                categories.pop();
                continue;
            }
            Matcher m = BOOLEAN.matcher(line);
            if (categories.size() != 1 || !CATEGORY.equals(categories.peek()) || !m.matches()) continue;
            boolean on = "true".equals(
                m.group(3)
                    .trim()
                    .toLowerCase(Locale.ROOT));
            String name = m.group(2)
                .trim();
            if (name.equals("export")) export = on;
            else if (name.equals("listener")) listener = on;
            else if (name.equals("client")) client = on;
        }
        if (!categories.isEmpty()) throw new IllegalArgumentException("unclosed {");
        return new ModulesConfig(export, listener, client);
    }

    @Override
    public String toString() {
        return "export=" + export + " listener=" + listener + " client=" + client;
    }
}
