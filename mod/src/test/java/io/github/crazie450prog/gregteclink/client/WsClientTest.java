package io.github.crazie450prog.gregteclink.client;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/** The RFC 6455 client against a hand-written server on loopback: handshake, frames, pings, fragments, close. */
class WsClientTest {

    private ServerSocket server;

    @BeforeEach
    void open() throws IOException {
        server = new ServerSocket(0);
    }

    @AfterEach
    void close() throws IOException {
        server.close();
    }

    /** The server side of one connection: reads the request, answers it, then runs {@code then}. */
    interface Script {

        void run(DataInputStream in, OutputStream out) throws Exception;
    }

    private CompletableFuture<String> serve(String status, boolean rightAccept, Script then) {
        return CompletableFuture.supplyAsync(() -> {
            try (Socket s = server.accept()) {
                s.setSoTimeout(5000);
                InputStream in = s.getInputStream();
                OutputStream out = s.getOutputStream();
                String head = readHead(in);
                String key = null;
                for (String line : head.split("\r\n")) {
                    if (line.toLowerCase()
                        .startsWith("sec-websocket-key:"))
                        key = line.substring(18)
                            .trim();
                }
                String accept = rightAccept ? WsClient.acceptFor(key) : "wrong";
                out.write(
                    ("HTTP/1.1 " + status
                        + "\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: "
                        + accept
                        + "\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
                out.flush();
                then.run(new DataInputStream(in), out);
                return head;
            } catch (Exception e) {
                throw new RuntimeException(e);
            }
        });
    }

    private static String readHead(InputStream in) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        while (!b.toString("ISO-8859-1")
            .endsWith("\r\n\r\n")) {
            int c = in.read();
            if (c < 0) throw new IOException("eof");
            b.write(c);
        }
        return b.toString("ISO-8859-1");
    }

    private static void frame(OutputStream out, int opcode, boolean fin, byte[] payload) throws IOException {
        out.write((fin ? 0x80 : 0) | opcode);
        if (payload.length < 126) out.write(payload.length);
        else {
            out.write(126);
            out.write(payload.length >>> 8);
            out.write(payload.length & 0xFF);
        }
        out.write(payload);
        out.flush();
    }

    /** One client frame: checks the mask bit and returns {opcode, unmasked payload}. */
    private static Object[] clientFrame(DataInputStream in) throws IOException {
        int b0 = in.readUnsignedByte();
        int b1 = in.readUnsignedByte();
        assertTrue((b1 & 0x80) != 0, "a client frame must be masked");
        int len = b1 & 0x7F;
        if (len == 126) len = in.readUnsignedShort();
        byte[] mask = new byte[4];
        in.readFully(mask);
        byte[] p = new byte[len];
        in.readFully(p);
        for (int i = 0; i < len; i++) p[i] ^= mask[i & 3];
        return new Object[] { b0 & 0x0F, p };
    }

    private WsClient connect() throws IOException {
        return WsClient.connect(
            "127.0.0.1",
            server.getLocalPort(),
            "/mod/ws",
            Collections.singletonMap("Authorization", "Bearer t0ken"),
            2000,
            5000);
    }

    @Test
    void handshakeTextPingFragmentsAndClose() throws Exception {
        CompletableFuture<String> srv = serve("101 Switching Protocols", true, (in, out) -> {
            frame(out, 0x1, true, "{\"type\":\"hello\"}".getBytes(StandardCharsets.UTF_8));
            frame(out, 0x9, true, new byte[] { 7, 8 }); // ping: the client must pong it
            Object[] pong = clientFrame(in);
            assertEquals(0xA, pong[0]);
            assertArrayEquals(new byte[] { 7, 8 }, (byte[]) pong[1]);
            Object[] text = clientFrame(in);
            assertEquals(0x1, text[0]);
            assertEquals("h\u00e9llo \u2713", new String((byte[]) text[1], StandardCharsets.UTF_8));
            frame(out, 0x1, false, "par".getBytes(StandardCharsets.UTF_8));
            frame(out, 0x0, true, "ts".getBytes(StandardCharsets.UTF_8));
            byte[] big = new byte[300];
            java.util.Arrays.fill(big, (byte) 'x');
            frame(out, 0x1, true, big);
            frame(out, 0x8, true, new byte[] { 0x03, (byte) 0xE8 });
            Object[] closeReply = clientFrame(in);
            assertEquals(0x8, closeReply[0]);
        });
        try (WsClient ws = connect()) {
            assertEquals("{\"type\":\"hello\"}", ws.read());
            Thread.sleep(50); // let the ping arrive; it is answered inside read()
            CompletableFuture<String> next = CompletableFuture.supplyAsync(() -> {
                try {
                    return ws.read();
                } catch (IOException e) {
                    throw new RuntimeException(e);
                }
            });
            Thread.sleep(100);
            ws.send("h\u00e9llo \u2713");
            assertEquals("parts", next.get(5, TimeUnit.SECONDS));
            assertEquals(
                300,
                ws.read()
                    .length());
            assertNull(ws.read()); // closed by the server
            assertTrue(!ws.isOpen());
        }
        String head = srv.get(5, TimeUnit.SECONDS);
        assertTrue(head.startsWith("GET /mod/ws HTTP/1.1\r\n"));
        assertTrue(head.contains("\r\nAuthorization: Bearer t0ken\r\n"));
        assertTrue(head.contains("\r\nHost: 127.0.0.1:" + server.getLocalPort() + "\r\n"));
    }

    @Test
    void aRefusedHandshakeThrows() {
        serve("403 Forbidden", true, (in, out) -> {});
        IOException e = assertThrows(IOException.class, this::connect);
        assertTrue(
            e.getMessage()
                .contains("403"));
    }

    @Test
    void aWrongAcceptThrows() {
        serve("101 Switching Protocols", false, (in, out) -> {});
        assertThrows(IOException.class, this::connect);
    }

    @Test
    void aMaskedServerFrameOrABinaryMessageThrows() throws Exception {
        serve("101 Switching Protocols", true, (in, out) -> {
            out.write(new byte[] { (byte) 0x81, (byte) 0x81, 1, 2, 3, 4, 5 });
            out.flush();
            Thread.sleep(200);
        });
        try (WsClient ws = connect()) {
            assertThrows(IOException.class, ws::read);
        }
    }

    @Test
    void anOversizedFrameThrows() throws Exception {
        serve("101 Switching Protocols", true, (in, out) -> {
            out.write(new byte[] { (byte) 0x81, 127, 0, 0, 0, 0, 0x7F, 0, 0, 0 });
            out.flush();
            Thread.sleep(200);
        });
        try (WsClient ws = connect()) {
            assertThrows(IOException.class, ws::read);
        }
    }

    @Test
    void theAcceptValueIsTheRfcExample() {
        // RFC 6455 section 1.3
        assertEquals("s3pPLMBiTxaQ9kYGzzhZRbK+xOo=", WsClient.acceptFor("dGhlIHNhbXBsZSBub25jZQ=="));
    }
}
