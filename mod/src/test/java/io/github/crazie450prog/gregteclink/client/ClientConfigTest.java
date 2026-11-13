package io.github.crazie450prog.gregteclink.client;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import com.google.gson.JsonElement;
import com.google.gson.JsonParser;

import io.github.crazie450prog.gregteclink.ModulesConfig;

/** The client category of gregteclink.cfg, and the messages' NBT step. */
class ClientConfigTest {

    @TempDir
    Path tmp;

    @Test
    void defaultsAndToggles() {
        ClientConfig on = ClientConfig.parse("modules {\n B:client=true\n}\n");
        assertTrue(on.position && on.target && on.chat && on.notices);
        assertEquals(8765, on.port);
        assertNull(on.tokenFile);
        ClientConfig off = ClientConfig.parse(
            "client {\n  B:position=false\n  B:target=FALSE\n  B:\"chat\"=false\n  B:notices=no\n"
                + "  I:port=18765\n  S:token_file=C:/Users/x/GregtecOS/config/gregteclink-play.token\n}\n");
        assertFalse(off.position || off.target || off.chat || off.notices);
        assertEquals(18765, off.port);
        assertEquals("C:/Users/x/GregtecOS/config/gregteclink-play.token", off.tokenFile);
        assertFalse(
            off.toString()
                .contains("GregtecOS"),
            "the path isn't logged");
    }

    @Test
    void onlyTheTopLevelClientCategory() {
        ClientConfig c = ClientConfig.parse("other {\n B:chat=false\n}\nclient {\n inner {\n  B:chat=false\n }\n}\n");
        assertTrue(c.chat);
    }

    @Test
    void badFilesThrowAndLoadTurnsEverythingOff() throws Exception {
        assertThrows(IllegalArgumentException.class, () -> ClientConfig.parse("client {\n I:port=0\n}\n"));
        assertThrows(IllegalArgumentException.class, () -> ClientConfig.parse("client {\n I:port=x\n}\n"));
        assertThrows(IllegalArgumentException.class, () -> ClientConfig.parse("client {\n"));
        File dir = tmp.toFile();
        ClientConfig missing = ClientConfig.load(dir);
        assertFalse(missing.position || missing.target || missing.chat || missing.notices);
        Files.write(tmp.resolve(ModulesConfig.FILE_NAME), "client {\n}\n}\n".getBytes(StandardCharsets.UTF_8));
        assertNull(ClientConfig.load(dir).tokenFile);
    }

    @Test
    void aRelativeTokenFileIsInTheGameFolder() {
        File game = tmp.toFile();
        assertEquals(
            new File(game, "x/t.token"),
            ClientConfig.parse("client {\n S:token_file=x/t.token\n}\n")
                .tokenFile(game));
        File abs = tmp.resolve("abs.token")
            .toAbsolutePath()
            .toFile();
        assertEquals(
            abs,
            ClientConfig.parse("client {\n S:token_file=" + abs.getPath() + "\n}\n")
                .tokenFile(game));
    }

    @Test
    void numericIdsBesideARegistryNameAreDropped() {
        JsonElement nbt = new JsonParser().parse(
            "{\"Items\":[{\"__item\":\"minecraft:stone\",\"id\":1,\"Count\":\"1B\",\"Damage\":\"0S\"}],"
                + "\"id\":5,\"list\":[{\"id\":3}]}");
        assertEquals(
            "{\"Items\":[{\"__item\":\"minecraft:stone\",\"Count\":\"1B\",\"Damage\":\"0S\"}],\"id\":5,"
                + "\"list\":[{\"id\":3}]}",
            LinkMessages.withoutIds(nbt)
                .toString());
    }

    @Test
    void statusRoundsToOneDecimal() {
        String s = LinkMessages.status(-1, "Nether", 1.26, 64.0, -3.04, true);
        assertEquals(
            "{\"type\":\"status\",\"v\":1,\"dim\":-1,\"dim_name\":\"Nether\",\"x\":1.3,\"y\":64.0,\"z\":-3.0,"
                + "\"paused\":true}",
            s);
    }
}
