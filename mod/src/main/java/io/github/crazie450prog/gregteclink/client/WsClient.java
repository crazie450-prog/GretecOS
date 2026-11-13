package io.github.crazie450prog.gregteclink.client;

import java.io.ByteArrayOutputStream;
import java.io.Closeable;
import java.io.DataInputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.Locale;
import java.util.Map;

/**
 * A minimal WebSocket client (RFC 6455) for the link to the core on loopback: text frames only, masked as a client
 * must, pings answered, fragments joined, at most {@link #MAX_MESSAGE} bytes a message. Java 8 has no WebSocket client
 * and the mod adds no dependency, so this is GregtecLink's own (MIT), written from the RFC.
 *
 * <p>
 * {@link #connect} does the opening handshake and checks {@code Sec-WebSocket-Accept}; {@link #read} returns the next
 * text message, or null once the connection is closed. {@link #send} may be called from any thread.
 */
public final class WsClient implements Closeable {

    public static final int MAX_MESSAGE = 64 * 1024;
    private static final String GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
    private static final int OP_CONT = 0x0, OP_TEXT = 0x1, OP_BINARY = 0x2, OP_CLOSE = 0x8, OP_PING = 0x9,
        OP_PONG = 0xA;
    private static final SecureRandom RANDOM = new SecureRandom();

    private final Socket socket;
    private final DataInputStream in;
    private final OutputStream out;
    private volatile boolean closed;

    private WsClient(Socket socket, InputStream in, OutputStream out) {
        this.socket = socket;
        this.in = new DataInputStream(in);
        this.out = out;
    }

    /**
     * Opens {@code ws://host:port/path} with these extra request headers. {@code readTimeoutMs} also bounds every
     * later {@link #read}: a core that sends nothing (not even its WebSocket pings) for that long is taken as gone.
     */
    public static WsClient connect(String host, int port, String path, Map<String, String> headers,
        int connectTimeoutMs, int readTimeoutMs) throws IOException {
        Socket s = new Socket();
        try {
            s.setTcpNoDelay(true);
            s.connect(new InetSocketAddress(host, port), connectTimeoutMs);
            s.setSoTimeout(readTimeoutMs);
            InputStream in = s.getInputStream();
            OutputStream out = s.getOutputStream();
            byte[] nonce = new byte[16];
            RANDOM.nextBytes(nonce);
            String key = Base64.getEncoder()
                .encodeToString(nonce);
            StringBuilder req = new StringBuilder();
            req.append("GET ")
                .append(path)
                .append(" HTTP/1.1\r\n");
            req.append("Host: ")
                .append(host)
                .append(':')
                .append(port)
                .append("\r\n");
            req.append("Upgrade: websocket\r\nConnection: Upgrade\r\n");
            req.append("Sec-WebSocket-Key: ")
                .append(key)
                .append("\r\nSec-WebSocket-Version: 13\r\n");
            for (Map.Entry<String, String> h : headers.entrySet()) {
                req.append(h.getKey())
                    .append(": ")
                    .append(h.getValue())
                    .append("\r\n");
            }
            req.append("\r\n");
            out.write(
                req.toString()
                    .getBytes(StandardCharsets.UTF_8));
            out.flush();
            String head = readHead(in);
            String[] lines = head.split("\r\n");
            String[] status = lines[0].split(" ", 3);
            if (status.length < 2 || !status[1].equals("101")) {
                throw new IOException("handshake refused: " + lines[0]);
            }
            String accept = null;
            for (int i = 1; i < lines.length; i++) {
                int colon = lines[i].indexOf(':');
                if (colon > 0 && lines[i].substring(0, colon)
                    .trim()
                    .toLowerCase(Locale.ROOT)
                    .equals("sec-websocket-accept")) {
                    accept = lines[i].substring(colon + 1)
                        .trim();
                }
            }
            if (!acceptFor(key).equals(accept)) throw new IOException("handshake: wrong Sec-WebSocket-Accept");
            return new WsClient(s, in, out);
        } catch (IOException | RuntimeException e) {
            try {
                s.close();
            } catch (IOException ignored) {}
            throw e;
        }
    }

    static String acceptFor(String key) {
        try {
            byte[] sha = MessageDigest.getInstance("SHA-1")
                .digest((key + GUID).getBytes(StandardCharsets.US_ASCII));
            return Base64.getEncoder()
                .encodeToString(sha);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    /** The response head up to the blank line, byte by byte so no frame byte is read with it. */
    private static String readHead(InputStream in) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        int matched = 0;
        while (matched < 4) {
            int c = in.read();
            if (c < 0) throw new EOFException("connection closed during the handshake");
            b.write(c);
            if (b.size() > 8192) throw new IOException("handshake response too long");
            matched = (c == (matched % 2 == 0 ? '\r' : '\n')) ? matched + 1 : (c == '\r' ? 1 : 0);
        }
        return new String(b.toByteArray(), StandardCharsets.ISO_8859_1);
    }

    /** Sends one text message. */
    public void send(String text) throws IOException {
        frame(OP_TEXT, text.getBytes(StandardCharsets.UTF_8));
    }

    private synchronized void frame(int opcode, byte[] payload) throws IOException {
        if (closed && opcode != OP_CLOSE) throw new IOException("closed");
        byte[] mask = new byte[4];
        RANDOM.nextBytes(mask);
        int n = payload.length;
        int head = n < 126 ? 2 : n < 65536 ? 4 : 10;
        byte[] f = new byte[head + 4 + n];
        f[0] = (byte) (0x80 | opcode);
        if (n < 126) {
            f[1] = (byte) (0x80 | n);
        } else if (n < 65536) {
            f[1] = (byte) (0x80 | 126);
            f[2] = (byte) (n >>> 8);
            f[3] = (byte) n;
        } else {
            f[1] = (byte) (0x80 | 127);
            for (int i = 0; i < 8; i++) f[2 + i] = (byte) ((long) n >>> (56 - 8 * i));
        }
        System.arraycopy(mask, 0, f, head, 4);
        for (int i = 0; i < n; i++) f[head + 4 + i] = (byte) (payload[i] ^ mask[i & 3]);
        out.write(f);
        out.flush();
    }

    /**
     * The next text message, or null when the core closed the connection. Answers pings; a binary message, a masked or
     * oversized frame, or a read timeout throws.
     */
    public String read() throws IOException {
        ByteArrayOutputStream message = null;
        while (true) {
            int b0 = in.read();
            if (b0 < 0) {
                closed = true;
                return null;
            }
            int b1 = in.readUnsignedByte();
            boolean fin = (b0 & 0x80) != 0;
            int opcode = b0 & 0x0F;
            if ((b1 & 0x80) != 0) throw new IOException("the server masked a frame");
            long len = b1 & 0x7F;
            if (len == 126) len = in.readUnsignedShort();
            else if (len == 127) len = in.readLong();
            if (len < 0 || len > MAX_MESSAGE) throw new IOException("frame too large: " + len);
            byte[] payload = new byte[(int) len];
            in.readFully(payload);
            switch (opcode) {
                case OP_PING:
                    frame(OP_PONG, payload);
                    break;
                case OP_PONG:
                    break;
                case OP_CLOSE:
                    if (!closed) {
                        closed = true;
                        try {
                            frame(OP_CLOSE, payload.length >= 2 ? new byte[] { payload[0], payload[1] } : new byte[0]);
                        } catch (IOException ignored) {}
                    }
                    return null;
                case OP_TEXT:
                case OP_CONT:
                    if (opcode == OP_TEXT) {
                        if (message != null) throw new IOException("a new message inside a fragmented one");
                        message = new ByteArrayOutputStream();
                    } else if (message == null) {
                        throw new IOException("a continuation frame with no message");
                    }
                    message.write(payload);
                    if (message.size() > MAX_MESSAGE) throw new IOException("message too large");
                    if (fin) return new String(message.toByteArray(), StandardCharsets.UTF_8);
                    break;
                case OP_BINARY:
                    throw new IOException("binary messages mean nothing here");
                default:
                    throw new IOException("unknown opcode " + opcode);
            }
        }
    }

    public boolean isOpen() {
        return !closed && !socket.isClosed();
    }

    /** Sends a close frame (best effort) and closes the socket. */
    @Override
    public void close() {
        if (!closed) {
            closed = true;
            try {
                frame(OP_CLOSE, new byte[] { 0x03, (byte) 0xE8 }); // 1000, normal closure
            } catch (IOException ignored) {}
        }
        try {
            socket.close();
        } catch (IOException ignored) {}
    }
}
