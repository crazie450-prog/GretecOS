package io.github.crazie450prog.gregteclink.export;

import java.io.BufferedWriter;
import java.io.Closeable;
import java.io.FilterOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.DigestOutputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

/** A UTF-8 output file that records its sha256, byte count and line count while it is written. */
final class HashedFile implements Closeable {

    final String name;
    private final MessageDigest digest;
    private final CountingStream counter;
    private final Writer out;
    private long lines;
    private String sha256;

    HashedFile(PathGuard guard, String dir, String name) throws IOException {
        this.name = name;
        Path path = guard.resolve(dir + "/" + name);
        Files.createDirectories(path.getParent());
        try {
            digest = MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException e) {
            throw new IOException(e);
        }
        OutputStream file = Files.newOutputStream(path, StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE);
        counter = new CountingStream(new DigestOutputStream(file, digest));
        out = new BufferedWriter(new OutputStreamWriter(counter, StandardCharsets.UTF_8), 1 << 16);
    }

    /** Writes one JSON value followed by a newline. */
    void line(JsonElement json) throws IOException {
        out.write(Json.COMPACT.toJson(json));
        out.write('\n');
        lines++;
    }

    /** Writes a whole pretty-printed JSON document. */
    void document(JsonElement json) throws IOException {
        String text = Json.PRETTY.toJson(json) + "\n";
        out.write(text);
        for (int i = 0; i < text.length(); i++) {
            if (text.charAt(i) == '\n') lines++;
        }
    }

    @Override
    public void close() throws IOException {
        if (sha256 != null) return;
        out.close();
        sha256 = Json.hex(digest.digest());
    }

    /** {"sha256","bytes","lines"} for the manifest; call after {@link #close()}. */
    JsonObject summary() {
        JsonObject o = new JsonObject();
        o.addProperty("sha256", sha256);
        o.addProperty("bytes", counter.count);
        o.addProperty("lines", lines);
        return o;
    }

    private static final class CountingStream extends FilterOutputStream {

        long count;

        CountingStream(OutputStream out) {
            super(out);
        }

        @Override
        public void write(int b) throws IOException {
            out.write(b);
            count++;
        }

        @Override
        public void write(byte[] b, int off, int len) throws IOException {
            out.write(b, off, len);
            count += len;
        }
    }
}
