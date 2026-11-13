package io.github.crazie450prog.gregteclink.client;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Collections;
import java.util.Iterator;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.LinkedBlockingDeque;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.Supplier;

import com.google.gson.JsonObject;

import io.github.crazie450prog.gregteclink.GregtecLink;

/**
 * The WebSocket to the core, on its own daemon thread: connects to {@code ws://127.0.0.1:<port>/mod/ws} with the
 * instance's token, sends {@code hello}, then queues what the core sends for the client thread ({@link #poll}). A lost
 * connection (the core restarted, stopped or was never up) is retried after 1, 2, 4, 8, then every 15 s, reading the
 * token file again each time; nothing is queued while it is down, so the core never gets stale positions. The token is
 * never logged. Sends are written by a second daemon thread from a small bounded queue, so a core that stalls with the
 * socket open never blocks the game's thread (review LOW-2): a full queue drops its oldest {@code status} first.
 */
public final class CoreLink {

    public static final String PATH = "/mod/ws";
    static final long[] BACKOFF_MS = { 1000, 2000, 4000, 8000, 15000 };
    static final int CONNECT_TIMEOUT_MS = 2000;
    /** The core pings every 20 s (uvicorn's default), so a minute of silence means it is gone. */
    static final int READ_TIMEOUT_MS = 60_000;
    static final int INBOUND_MAX = 256;
    /** Messages waiting to be written: half a minute of status, or a few key reports and chat lines. */
    static final int OUTBOUND_MAX = 32;

    /** One connection: what {@link WsClient} does, replaceable in tests. */
    interface Conn {

        void send(String text) throws IOException;

        /** The next message, or null once closed. */
        String read() throws IOException;

        void close();
    }

    interface Connector {

        Conn open(String token) throws IOException;
    }

    private final Connector connector;
    private final Supplier<String> token;
    private final Supplier<String> hello;
    private final long[] backoffMs;
    private final ConcurrentLinkedQueue<JsonObject> inbound = new ConcurrentLinkedQueue<>();
    /** {type, text} pairs for the writer thread; added to under its own lock so its size stays bounded. */
    private final LinkedBlockingDeque<String[]> outbound = new LinkedBlockingDeque<>();
    private final AtomicInteger dropped = new AtomicInteger();
    private final Map<String, AtomicInteger> sent = new ConcurrentHashMap<>();
    private final AtomicInteger connects = new AtomicInteger();
    private volatile Conn conn;
    private volatile boolean stopped;
    private Thread thread, writer;

    CoreLink(Connector connector, Supplier<String> token, Supplier<String> hello, long[] backoffMs) {
        this.connector = connector;
        this.token = token;
        this.hello = hello;
        this.backoffMs = backoffMs;
    }

    /** The real link: loopback only, the token read from {@code tokenFile} at each attempt. */
    public static CoreLink create(int port, File tokenFile, Supplier<String> hello) {
        Connector c = t -> {
            WsClient ws = WsClient.connect(
                "127.0.0.1",
                port,
                PATH,
                Collections.singletonMap("Authorization", "Bearer " + t),
                CONNECT_TIMEOUT_MS,
                READ_TIMEOUT_MS);
            return new Conn() {

                @Override
                public void send(String text) throws IOException {
                    ws.send(text);
                }

                @Override
                public String read() throws IOException {
                    return ws.read();
                }

                @Override
                public void close() {
                    ws.close();
                }
            };
        };
        return new CoreLink(c, () -> readToken(tokenFile), hello, BACKOFF_MS);
    }

    /** The file's first line, trimmed; null when it is missing, unreadable or empty. */
    static String readToken(File f) {
        try {
            String s = new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8).trim();
            int nl = s.indexOf('\n');
            if (nl >= 0) s = s.substring(0, nl)
                .trim();
            return s.isEmpty() ? null : s;
        } catch (IOException | RuntimeException e) {
            return null;
        }
    }

    public synchronized void start() {
        if (thread != null) return;
        thread = new Thread(this::run, "GregtecLink core link");
        thread.setDaemon(true);
        thread.start();
        writer = new Thread(this::write, "GregtecLink core link writer");
        writer.setDaemon(true);
        writer.start();
    }

    public void stop() {
        stopped = true;
        Conn c = conn;
        if (c != null) c.close();
        Thread t = thread;
        if (t != null) t.interrupt();
        Thread w = writer;
        if (w != null) w.interrupt();
    }

    private void run() {
        int failures = 0;
        boolean reported = false; // one log line per outage
        while (!stopped) {
            String t = token.get();
            if (t == null) {
                if (!reported) GregtecLink.LOG.warn("client link: no token in the token file; retrying");
                reported = true;
            } else {
                Conn c = null;
                try {
                    c = connector.open(t);
                    c.send(hello.get());
                    conn = c;
                    connects.incrementAndGet();
                    failures = 0;
                    reported = false;
                    GregtecLink.LOG.info("client link: connected to the core");
                    String text;
                    while (!stopped && (text = c.read()) != null) {
                        JsonObject o = LinkMessages.parse(text);
                        if (o != null && inbound.size() < INBOUND_MAX) inbound.add(o);
                    }
                    if (!stopped) GregtecLink.LOG.info("client link: the core closed the connection");
                } catch (IOException | RuntimeException e) {
                    if (!reported && !stopped)
                        GregtecLink.LOG.info("client link: core not reachable ({})", e.toString());
                    reported = true;
                } finally {
                    conn = null;
                    outbound.clear(); // nothing waits for the next connection
                    if (c != null) c.close();
                }
            }
            if (stopped) break;
            try {
                Thread.sleep(backoffMs[Math.min(failures++, backoffMs.length - 1)]);
            } catch (InterruptedException e) {
                break;
            }
        }
    }

    /**
     * Queues one message for the writer thread if connected; false (nothing queued) when not. Never blocks: with
     * {@link #OUTBOUND_MAX} waiting, the oldest {@code status} (else the oldest message) is dropped for it.
     */
    public boolean send(String type, String text) {
        if (conn == null) return false;
        synchronized (outbound) {
            if (outbound.size() >= OUTBOUND_MAX) dropOne();
            outbound.addLast(new String[] { type, text });
        }
        return true;
    }

    private void dropOne() {
        dropped.incrementAndGet();
        for (Iterator<String[]> it = outbound.iterator(); it.hasNext();) {
            if ("status".equals(it.next()[0])) {
                it.remove();
                return;
            }
        }
        outbound.pollFirst();
    }

    /** The writer thread: each queued message to the connection of the moment; {@code type} counts it once written. */
    private void write() {
        while (!stopped) {
            String[] m;
            try {
                m = outbound.takeFirst();
            } catch (InterruptedException e) {
                break;
            }
            Conn c = conn;
            if (c == null) continue;
            try {
                c.send(m[1]);
                sent.computeIfAbsent(m[0], k -> new AtomicInteger())
                    .incrementAndGet();
            } catch (IOException e) {
                c.close(); // the reader sees the close and reconnects
            }
        }
    }

    /** The next message from the core, or null. */
    public JsonObject poll() {
        return inbound.poll();
    }

    public boolean connected() {
        return conn != null;
    }

    /** Successful connections so far (2 after one reconnect). */
    public int connects() {
        return connects.get();
    }

    /** Messages dropped because the queue was full (a stalled core). */
    public int dropped() {
        return dropped.get();
    }

    /** Messages of {@code type} sent so far. */
    public int sent(String type) {
        AtomicInteger n = sent.get(type);
        return n == null ? 0 : n.get();
    }
}
