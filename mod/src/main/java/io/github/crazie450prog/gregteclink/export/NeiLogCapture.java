package io.github.crazie450prog.gregteclink.export;

import java.io.Serializable;

import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.core.Filter;
import org.apache.logging.log4j.core.Layout;
import org.apache.logging.log4j.core.LogEvent;
import org.apache.logging.log4j.core.Logger;
import org.apache.logging.log4j.core.appender.AbstractAppender;

/**
 * Captures NEI's {@code NotEnoughItems} logger for the duration of one {@code dumpAll} call, into a {@link NeiLog}.
 * Errors arrive from NEI's ForkJoin threads too, so {@link #append} is synchronized. Only API that exists both in
 * Minecraft's log4j 2.0-beta9 and in the log4j 2.x of the Java 17+ runtime is used.
 */
final class NeiLogCapture extends AbstractAppender {

    static final String LOGGER = "NotEnoughItems";

    private final Logger logger;
    private final NeiLog log = new NeiLog();

    private NeiLogCapture(Logger logger) {
        super("gregteclink-nei-capture", (Filter) null, (Layout<? extends Serializable>) null);
        this.logger = logger;
    }

    /** Adds the capture to NEI's logger, or throws if that logger isn't log4j core's. */
    static NeiLogCapture attach() {
        Object l = LogManager.getLogger(LOGGER);
        if (!(l instanceof Logger)) {
            throw new IllegalStateException(
                "NEI's logger is a " + l.getClass()
                    .getName() + ", not log4j core's Logger");
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
