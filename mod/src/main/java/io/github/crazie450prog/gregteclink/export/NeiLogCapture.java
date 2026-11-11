package io.github.crazie450prog.gregteclink.export;

import java.io.Serializable;

import org.apache.logging.log4j.core.Filter;
import org.apache.logging.log4j.core.Layout;
import org.apache.logging.log4j.core.LogEvent;
import org.apache.logging.log4j.core.Logger;
import org.apache.logging.log4j.core.appender.AbstractAppender;

/**
 * Captures the logger NEI writes to ({@code NEIClientConfig.logger}, named {@code NotEnoughItems}) for the duration of
 * one {@code dumpAll} call (into a {@link NeiLog}) or of the crop step's dumper calls (into {@link Lines}).
 * NEI's other threads (its ForkJoin pool) may log during a call too, so {@link #append} is synchronized. Only API
 * that exists both in Minecraft's log4j 2.0-beta9 and in the log4j 2.x of the Java 17+ runtime is used.
 */
final class NeiLogCapture extends AbstractAppender {

    static final String NEI_CONFIG = "codechicken.nei.NEIClientConfig";

    /** Receives each captured event: its level name, formatted message, and throwable summary (or null). */
    interface Sink {

        void add(String level, String message, String thrown);
    }

    /** The ERROR lines whose message starts with a prefix: how many, and the first one with its throwable. */
    static final class Lines implements Sink {

        private final String prefix;
        private int count;
        private String first;

        Lines(String prefix) {
            this.prefix = prefix;
        }

        @Override
        public void add(String level, String message, String thrown) {
            if (!"ERROR".equals(level) || message == null || !message.startsWith(prefix)) return;
            if (count++ == 0) first = message + (thrown == null ? "" : ": " + thrown);
        }

        int count() {
            return count;
        }

        String first() {
            return first;
        }
    }

    private final Logger logger;
    private final Sink log;

    private NeiLogCapture(Logger logger, Sink log) {
        super("gregteclink-nei-capture", (Filter) null, (Layout<? extends Serializable>) null);
        this.logger = logger;
        this.log = log;
    }

    /**
     * Adds the capture to the logger object NEI itself writes to (read from its public static field, not looked up by
     * name, so it is the same logger whatever log context GregtecLink is in), or throws if that isn't log4j core's.
     */
    static NeiLogCapture attach(Sink log) throws ReflectiveOperationException {
        Object l = Class.forName(NEI_CONFIG)
            .getField("logger")
            .get(null);
        if (!(l instanceof Logger)) {
            throw new IllegalStateException(
                "NEIClientConfig.logger is " + (l == null ? "null"
                    : "a " + l.getClass()
                        .getName())
                    + ", not log4j core's Logger");
        }
        NeiLogCapture c = new NeiLogCapture((Logger) l, log);
        c.start();
        c.logger.addAppender(c);
        return c;
    }

    /** Synchronized, so the sink is read after any event still being appended from another thread. */
    synchronized void detach() {
        logger.removeAppender(this);
    }

    @Override
    public synchronized void append(LogEvent e) {
        Throwable t = e.getThrown();
        log.add(
            String.valueOf(e.getLevel()),
            e.getMessage() == null ? null
                : e.getMessage()
                    .getFormattedMessage(),
            t == null ? null : Json.error(t));
    }
}
