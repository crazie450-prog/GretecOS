package io.github.crazie450prog.gregteclink.client;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import io.github.crazie450prog.gregteclink.GregtecLink;
import io.github.crazie450prog.gregteclink.ModulesConfig;

/**
 * The client module's settings: the {@code client} category of {@code config/gregteclink.cfg}, read only, as
 * {@link ModulesConfig} reads its {@code modules} category (D285).
 *
 * <pre>
 * client {
 *     B:position=true    # position and dimension at 1 Hz
 *     B:target=true      # the hovered item or looked-at block, on the key press or when the core asks
 *     B:chat=true        # chat lines
 *     B:notices=true     # GOS's local chat notices (seen only here, never sent to the server)
 *     I:port=8765        # the core's port on 127.0.0.1
 *     S:token_file=C:/Users/you/GregtecOS/config/gregteclink-play.token
 * }
 * </pre>
 *
 * Each feed defaults to on once the module is on; the port to 8765. There is no default token file: without one the
 * module does nothing. A relative {@code token_file} is relative to the game folder.
 */
public final class ClientConfig {

    public static final String CATEGORY = "client";
    public static final int DEFAULT_PORT = 8765;
    private static final Pattern LINE = Pattern.compile("([BIS]):(\"?)([^\"=]+)\\2=(.*)");

    public final boolean position;
    public final boolean target;
    public final boolean chat;
    public final boolean notices;
    public final int port;
    /** The file holding the core's token for this instance, or null when none is set. */
    public final String tokenFile;

    ClientConfig(boolean position, boolean target, boolean chat, boolean notices, int port, String tokenFile) {
        this.position = position;
        this.target = target;
        this.chat = chat;
        this.notices = notices;
        this.port = port;
        this.tokenFile = tokenFile;
    }

    /** Everything off: what a missing or unreadable file gives. */
    static ClientConfig off() {
        return new ClientConfig(false, false, false, false, DEFAULT_PORT, null);
    }

    public static ClientConfig load(File configDir) {
        File file = new File(configDir, ModulesConfig.FILE_NAME);
        try {
            return parse(new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8));
        } catch (Throwable t) {
            GregtecLink.LOG.warn("{} not read ({}): the client module stays off", file, t.toString());
            return off();
        }
    }

    /** Forge's config syntax as {@link ModulesConfig#parse} reads it; a bad port throws. */
    static ClientConfig parse(String text) {
        boolean position = true, target = true, chat = true, notices = true;
        int port = DEFAULT_PORT;
        String tokenFile = null;
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
            Matcher m = LINE.matcher(line);
            if (categories.size() != 1 || !CATEGORY.equals(categories.peek()) || !m.matches()) continue;
            String type = m.group(1);
            String name = m.group(3)
                .trim();
            String value = m.group(4)
                .trim();
            if (type.equals("B")) {
                boolean on = "true".equals(value.toLowerCase(Locale.ROOT));
                if (name.equals("position")) position = on;
                else if (name.equals("target")) target = on;
                else if (name.equals("chat")) chat = on;
                else if (name.equals("notices")) notices = on;
            } else if (type.equals("I") && name.equals("port")) {
                port = Integer.parseInt(value);
                if (port < 1 || port > 65535) throw new IllegalArgumentException("port out of range: " + port);
            } else if (type.equals("S") && name.equals("token_file")) {
                tokenFile = value.isEmpty() ? null : value;
            }
        }
        if (!categories.isEmpty()) throw new IllegalArgumentException("unclosed {");
        return new ClientConfig(position, target, chat, notices, port, tokenFile);
    }

    /** The token file, a relative one resolved against the game folder; null when none is set. */
    public File tokenFile(File gameDir) {
        if (tokenFile == null) return null;
        File f = new File(tokenFile);
        return f.isAbsolute() ? f : new File(gameDir, tokenFile);
    }

    @Override
    public String toString() {
        return "position=" + position
            + " target="
            + target
            + " chat="
            + chat
            + " notices="
            + notices
            + " port="
            + port
            + " token_file="
            + (tokenFile == null ? "(none)" : "set");
    }
}
