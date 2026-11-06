package io.github.crazie450prog.gregteclink.export;

/**
 * Waits on the client tick for NEI to be ready, lets it settle for {@link #SETTLE_TICKS} more ticks, announces the
 * dump on one tick (so the chat line is drawn before the game freezes) and dumps on the next; or times out. NEI going
 * back to not ready (an item-list reload) restarts the settle.
 */
final class NeiWait {

    enum Step {
        WAIT,
        ANNOUNCE,
        DUMP,
        TIMEOUT
    }

    static final int SETTLE_TICKS = 100;

    private final long startMs;
    private final long timeoutMs;
    private int readyTicks;
    private boolean done;

    NeiWait(long startMs, long timeoutMs) {
        this.startMs = startMs;
        this.timeoutMs = timeoutMs;
    }

    /** One client tick. Returns DUMP or TIMEOUT once; WAIT on every tick after that. */
    Step tick(boolean ready, long nowMs) {
        if (done) return Step.WAIT;
        if (!ready) {
            readyTicks = 0;
            if (nowMs - startMs >= timeoutMs) {
                done = true;
                return Step.TIMEOUT;
            }
            return Step.WAIT;
        }
        readyTicks++;
        if (readyTicks <= SETTLE_TICKS) return Step.WAIT;
        if (readyTicks == SETTLE_TICKS + 1) return Step.ANNOUNCE;
        done = true;
        return Step.DUMP;
    }
}
