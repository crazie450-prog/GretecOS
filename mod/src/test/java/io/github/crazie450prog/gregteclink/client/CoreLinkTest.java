package io.github.crazie450prog.gregteclink.client;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.BooleanSupplier;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import com.google.gson.JsonObject;

/** The link's thread with a fake connection: hello first, messages queued, reconnects, nothing sent while down. */
class CoreLinkTest {

    @TempDir
    Path tmp;

    /** A connection the test drives: what the core "sends" goes in {@code incoming}; null ends it. */
    static final class FakeConn implements CoreLink.Conn {

        final List<String> sent = new CopyOnWriteArrayList<>();
        final BlockingQueue<String> incoming = new LinkedBlockingQueue<>();
        volatile boolean closed;

        @Override
        public void send(String text) throws IOException {
            if (closed) throw new IOException("closed");
            sent.add(text);
        }

        @Override
        public String read() throws IOException {
            try {
                String s = incoming.take();
                return s.isEmpty() ? null : s; // "" stands for the core closing the connection
            } catch (InterruptedException e) {
                throw new IOException(e);
            }
        }

        @Override
        public void close() {
            closed = true;
            incoming.offer("");
        }
    }

    static void await(BooleanSupplier ok) throws InterruptedException {
        long end = System.nanoTime() + TimeUnit.SECONDS.toNanos(5);
        while (!ok.getAsBoolean()) {
            if (System.nanoTime() > end) throw new AssertionError("timed out");
            Thread.sleep(5);
        }
    }

    @Test
    void helloFirstThenMessagesThenAReconnectAfterTheCoreRestarts() throws Exception {
        BlockingQueue<FakeConn> conns = new LinkedBlockingQueue<>();
        AtomicInteger opens = new AtomicInteger();
        List<String> tokens = new CopyOnWriteArrayList<>();
        CoreLink link = new CoreLink(t -> {
            tokens.add(t);
            if (opens.incrementAndGet() == 2) throw new IOException("connection refused"); // the core is restarting
            FakeConn c = new FakeConn();
            conns.add(c);
            return c;
        }, () -> "tok", () -> "{\"type\":\"hello\"}", new long[] { 10 });
        assertFalse(link.send("status", "{}")); // not connected yet: nothing queued
        link.start();
        FakeConn first = conns.poll(5, TimeUnit.SECONDS);
        await(link::connected);
        assertEquals("{\"type\":\"hello\"}", first.sent.get(0));
        assertTrue(link.send("status", "{\"type\":\"status\"}"));
        assertEquals(1, link.sent("status"));
        first.incoming.add("{\"type\":\"notice\",\"text\":\"hi\"}");
        first.incoming.add("not json");
        first.incoming.add("{\"no\":\"type\"}");
        await(() -> link.poll() != null || false);
        assertNull(link.poll()); // the two bad ones were dropped
        first.incoming.add(""); // the core went away
        FakeConn second = conns.poll(5, TimeUnit.SECONDS);
        await(() -> link.connects() == 2);
        assertEquals("{\"type\":\"hello\"}", second.sent.get(0));
        assertEquals(3, opens.get()); // one refused attempt in between
        assertTrue(link.send("status", "{}"));
        link.stop();
        assertEquals(3, tokens.size());
    }

    @Test
    void noTokenNoConnection() throws Exception {
        AtomicInteger opens = new AtomicInteger();
        AtomicReference<String> token = new AtomicReference<>();
        CoreLink link = new CoreLink(t -> {
            opens.incrementAndGet();
            return new FakeConn();
        }, token::get, () -> "{}", new long[] { 10 });
        link.start();
        Thread.sleep(100);
        assertEquals(0, opens.get());
        token.set("now-there-is-one"); // read again at each attempt
        await(link::connected);
        link.stop();
    }

    @Test
    void theTokenFileIsItsFirstLineTrimmed() throws IOException {
        Path f = tmp.resolve("t.token");
        assertNull(CoreLink.readToken(f.toFile()));
        Files.write(f, "  abc-123 \r\nsecond\n".getBytes(StandardCharsets.UTF_8));
        assertEquals("abc-123", CoreLink.readToken(f.toFile()));
        Files.write(f, "\n".getBytes(StandardCharsets.UTF_8));
        assertNull(CoreLink.readToken(f.toFile()));
    }

    @Test
    void aFailedSendDropsTheConnection() throws Exception {
        FakeConn c = new FakeConn();
        CoreLink link = new CoreLink(t -> c, () -> "t", () -> "{}", new long[] { 10_000 });
        link.start();
        await(link::connected);
        c.closed = true;
        assertFalse(link.send("chat", "{}"));
        await(() -> !link.connected());
        assertEquals(0, link.sent("chat"));
        JsonObject none = link.poll();
        assertNull(none);
        link.stop();
    }
}
