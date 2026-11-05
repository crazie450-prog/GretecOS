package io.github.crazie450prog.gregteclink;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** The module switches are read only: no file is ever created, renamed or rewritten. */
class ModulesConfigTest {

    @TempDir
    Path tmp;

    private static final String EXPORT_ONLY = "# GregtecLink\n\nmodules {\n    B:client=false\n    B:export=true\n"
        + "    B:listener=false\n}\n";

    @Test
    void readsForgesFormat() {
        assertEquals(
            "export=true listener=false client=false",
            ModulesConfig.parse(EXPORT_ONLY)
                .toString());
        assertEquals(
            "export=false listener=true client=true",
            ModulesConfig.parse("modules {\r\n  B:\"listener\"=TRUE\r\n  B:client=true\r\n}\r\n")
                .toString());
    }

    @Test
    void ignoresOtherCategoriesAndLines() {
        String text = "other {\n    B:export=true\n}\nmodules {\n    I:export=1\n    B:export=yes\n"
            + "    nested {\n        B:client=true\n    }\n}\n";
        assertEquals(
            "export=false listener=false client=false",
            ModulesConfig.parse(text)
                .toString());
    }

    @Test
    void unbalancedBracesThrow() {
        assertThrows(IllegalArgumentException.class, () -> ModulesConfig.parse("modules {\n    B:export=true\n"));
        assertThrows(IllegalArgumentException.class, () -> ModulesConfig.parse("}\n"));
    }

    @Test
    void missingFileIsAllOffAndNotCreated() throws IOException {
        assertEquals(
            "export=false listener=false client=false",
            ModulesConfig.load(tmp.toFile())
                .toString());
        assertEquals(0, list(tmp).length);
    }

    @Test
    void malformedFileIsAllOffAndLeftAsItIs() throws IOException {
        byte[] bad = "modules {\n    B:export=true\n".getBytes(StandardCharsets.UTF_8);
        Path cfg = Files.write(tmp.resolve(ModulesConfig.FILE_NAME), bad);
        assertEquals(
            "export=false listener=false client=false",
            ModulesConfig.load(tmp.toFile())
                .toString());
        assertEquals(Arrays.asList(ModulesConfig.FILE_NAME), Arrays.asList(list(tmp)));
        assertEquals(
            new String(bad, StandardCharsets.UTF_8),
            new String(Files.readAllBytes(cfg), StandardCharsets.UTF_8));
    }

    @Test
    void validFileIsReadAndLeftAsItIs() throws IOException {
        Path cfg = Files.write(tmp.resolve(ModulesConfig.FILE_NAME), EXPORT_ONLY.getBytes(StandardCharsets.UTF_8));
        long mtime = Files.getLastModifiedTime(cfg)
            .toMillis();
        assertEquals(
            "export=true listener=false client=false",
            ModulesConfig.load(tmp.toFile())
                .toString());
        assertEquals(Arrays.asList(ModulesConfig.FILE_NAME), Arrays.asList(list(tmp)));
        assertEquals(
            mtime,
            Files.getLastModifiedTime(cfg)
                .toMillis());
    }

    private static String[] list(Path dir) {
        String[] names = new File(dir.toString()).list();
        Arrays.sort(names);
        return names;
    }
}
