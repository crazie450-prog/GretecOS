package io.github.crazie450prog.gregteclink.export;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.TimeZone;

import net.minecraft.launchwrapper.Launch;
import net.minecraftforge.common.ForgeVersion;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import cpw.mods.fml.common.FMLCommonHandler;
import cpw.mods.fml.common.Loader;
import cpw.mods.fml.common.ModContainer;
import gregtech.GTMod;
import io.github.crazie450prog.gregteclink.Tags;

/**
 * (d) of plan §5.2 step 2: manifest.json, written last. It lists Export's own mods (Play's come from Python's
 * fingerprint, never from here), versions, every file's sha256, counts and the status of each part.
 */
final class Manifest {

    private final ExportRun run;
    private final Map<String, String> hashCache = new HashMap<>();

    Manifest(ExportRun run) {
        this.run = run;
    }

    JsonObject build(boolean complete) throws IOException {
        JsonObject m = new JsonObject();
        m.addProperty("format", ExportRun.FORMAT);
        m.addProperty("run_id", run.runId);
        SimpleDateFormat iso = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'");
        iso.setTimeZone(TimeZone.getTimeZone("UTC"));
        m.addProperty("created_utc", iso.format(run.created));
        m.addProperty("complete", complete);
        m.addProperty("mode", run.mode);
        m.add("world", Json.str(run.world));
        m.addProperty(
            "side",
            FMLCommonHandler.instance()
                .getSide()
                .isClient() ? "client" : "server");
        m.addProperty("deobf_env", Boolean.TRUE.equals(Launch.blackboard.get("fml.deobfuscatedEnvironment")));
        m.addProperty("minecraft", Loader.MC_VERSION);
        m.addProperty("forge", ForgeVersion.getVersion());
        JsonObject java = new JsonObject();
        java.addProperty("version", System.getProperty("java.version"));
        java.addProperty("vendor", System.getProperty("java.vendor"));
        m.add("java", java);
        m.add("pack_version", Json.str(packVersion()));
        m.add("gt_version", Json.str(modVersion("gregtech")));
        m.add("nei_version", Json.str(modVersion("NotEnoughItems")));
        JsonObject self = new JsonObject();
        self.addProperty("version", Tags.VERSION);
        m.add("gregteclink", self);
        m.add("mods", mods());

        JsonObject files = new JsonObject();
        for (HashedFile f : run.files) files.add(f.name, f.summary());
        m.add("files", files);
        JsonObject counts = new JsonObject();
        for (Map.Entry<String, Number> e : run.counts.entrySet()) counts.addProperty(e.getKey(), e.getValue());
        m.add("counts", counts);
        JsonObject options = new JsonObject();
        options.addProperty("recipe_owners", GTMod.proxy.mNEIRecipeOwner);
        m.add("options", options);
        JsonObject parts = new JsonObject();
        for (Map.Entry<String, String> e : run.parts.entrySet()) parts.addProperty(e.getKey(), e.getValue());
        m.add("parts", parts);
        if (!run.partErrors.isEmpty()) {
            JsonObject errors = new JsonObject();
            for (Map.Entry<String, String> e : run.partErrors.entrySet()) errors.addProperty(e.getKey(), e.getValue());
            m.add("part_errors", errors);
        }
        JsonObject durations = new JsonObject();
        for (Map.Entry<String, Long> e : run.durations.entrySet()) durations.addProperty(e.getKey(), e.getValue());
        m.add("durations_ms", durations);
        return m;
    }

    private static String modVersion(String modid) {
        ModContainer mc = Loader.instance()
            .getIndexedModList()
            .get(modid);
        return mc == null ? null : mc.getVersion();
    }

    /** The raw S:ModPackVersion of config/GTNewHorizons/dreamcraft.cfg, or null. "2.9.x" means unknown (A§5.5). */
    private String packVersion() {
        File cfg = new File(run.gameDir, "config/GTNewHorizons/dreamcraft.cfg");
        if (!cfg.isFile()) return null;
        try {
            for (String line : Files.readAllLines(cfg.toPath(), StandardCharsets.UTF_8)) {
                String s = line.trim();
                if (s.startsWith("S:ModPackVersion=")) return s.substring("S:ModPackVersion=".length());
            }
        } catch (Throwable t) {
            return null;
        }
        return null;
    }

    private JsonArray mods() {
        List<ModContainer> list = new ArrayList<>(
            Loader.instance()
                .getActiveModList());
        list.sort(Comparator.comparing(ModContainer::getModId));
        Path game = run.gameDir.toPath()
            .toAbsolutePath()
            .normalize();
        JsonArray a = new JsonArray();
        for (ModContainer mc : list) {
            JsonObject o = new JsonObject();
            o.addProperty("modid", mc.getModId());
            o.addProperty("name", mc.getName());
            o.add("version", Json.str(mc.getVersion()));
            File src = mc.getSource();
            JsonElement file = JsonNull.INSTANCE;
            JsonElement sha = JsonNull.INSTANCE;
            if (src != null) {
                Path p = src.toPath()
                    .toAbsolutePath()
                    .normalize();
                // A path outside the game dir (dev runtime, Gradle cache) is reduced to its file name.
                file = Json.str(
                    p.startsWith(game) ? game.relativize(p)
                        .toString()
                        .replace('\\', '/')
                        : p.getFileName()
                            .toString());
                if (src.isFile()) sha = Json.str(sha256(p));
            }
            o.add("file", file);
            o.add("sha256", sha);
            a.add(o);
        }
        return a;
    }

    private String sha256(Path p) {
        String key = p.toString();
        if (hashCache.containsKey(key)) return hashCache.get(key);
        String hex = null;
        try (InputStream in = Files.newInputStream(p)) {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] buf = new byte[1 << 16];
            int n;
            while ((n = in.read(buf)) > 0) md.update(buf, 0, n);
            hex = Json.hex(md.digest());
        } catch (Throwable ignored) {
            // null: unreadable jar.
        }
        hashCache.put(key, hex);
        return hex;
    }
}
