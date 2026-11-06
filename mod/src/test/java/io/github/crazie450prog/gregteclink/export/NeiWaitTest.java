package io.github.crazie450prog.gregteclink.export;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class NeiWaitTest {

    @Test
    void settlesThenAnnouncesThenDumpsOnce() {
        NeiWait w = new NeiWait(0, 1000);
        for (int i = 0; i < NeiWait.SETTLE_TICKS; i++) assertEquals(NeiWait.Step.WAIT, w.tick(true, i));
        assertEquals(NeiWait.Step.ANNOUNCE, w.tick(true, 200));
        assertEquals(NeiWait.Step.DUMP, w.tick(true, 201));
        assertEquals(NeiWait.Step.WAIT, w.tick(true, 202));
    }

    @Test
    void notReadyAgainRestartsTheSettle() {
        NeiWait w = new NeiWait(0, 1000);
        for (int i = 0; i < NeiWait.SETTLE_TICKS - 1; i++) w.tick(true, i);
        assertEquals(NeiWait.Step.WAIT, w.tick(false, 100)); // an item-list reload
        for (int i = 0; i < NeiWait.SETTLE_TICKS; i++) assertEquals(NeiWait.Step.WAIT, w.tick(true, 101 + i));
        assertEquals(NeiWait.Step.ANNOUNCE, w.tick(true, 300));
    }

    @Test
    void timesOutOnceWhenNeiNeverLoads() {
        NeiWait w = new NeiWait(5000, 1000);
        assertEquals(NeiWait.Step.WAIT, w.tick(false, 5999));
        assertEquals(NeiWait.Step.TIMEOUT, w.tick(false, 6000));
        assertEquals(NeiWait.Step.WAIT, w.tick(true, 6001));
    }
}
