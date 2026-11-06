package io.github.crazie450prog.gregteclink.export;

import java.io.Serializable;

import org.apache.logging.log4j.core.Filter;
import org.apache.logging.log4j.core.Layout;
import org.apache.logging.log4j.core.LogEvent;
import org.apache.logging.log4j.core.Logger;
import org.apache.logging.log4j.core.appender.AbstractAppender;

/**
 * Captures the logger NEI writes to ({@code NEIClientConfig.logger}, named {@code NotEnoughItems}) for the duration of
 * one {@code dumpAll} call, into a {@link NeiLog}.
 * Errors arrive from NEI's ForkJoin threads too, so {@link #append} is synchronized. Only API that exists both in
 * Minecraft's log4j 2.0-beta9 and in the log4j 2.x of the Java 17+ runtime is used.
 */
final class NeiLogCapture extends AbstractAppender {

    static final String NEI_CONFIG = "codechicken.nei.NEIClientConfig";

    private final Logger logger;
    private final NeiLog log = new NeiLog();

    private NeiLogCapture(Logger logger) {
        super("gregteclink-nei-capture", (Filter) null, (Layout<? extends Serializable>) null);
        this.logger = logger;
    }

    /**
     * Adds the capture to the logger object NEI itself writes to (read from its public static field, not looked up by
     * name, so it is the same logger whatever log context GregtecLink is in), or throws if that isn't log4j core's.
     */
    static NeiLogCapture attach() throws ReflectiveOperationException {
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
        NeiLogCapture c = new NeiLogCapture((Logger) l);
        c.start();
        c.logger.addAppender(c);
        return c;
    }

    void detach() {
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

    synchronized NeiLog result() {
        return log;
    }
}
