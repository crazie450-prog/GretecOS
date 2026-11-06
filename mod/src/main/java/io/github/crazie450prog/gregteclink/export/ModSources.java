package io.github.crazie450prog.gregteclink.export;

import java.io.File;
import java.net.URL;
import java.security.CodeSource;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import cpw.mods.fml.common.Loader;
import cpw.mods.fml.common.ModContainer;

/**
 * Finds the mod that owns a class: the mod whose source (jar or dir) holds the class's CodeSource. When several mods
 * share one jar (GT5-Unofficial holds many), the mod whose own class package is the longest prefix of the class's
 * package wins.
 */
final class ModSources {

    private final Map<String, List<ModContainer>> bySource = new HashMap<>();
    private final Map<String, String> cache = new HashMap<>();

    ModSources() {
        for (ModContainer mc : Loader.instance()
            .getActiveModList()) {
            String key = key(mc.getSource());
            if (key == null) continue;
            List<ModContainer> list = bySource.get(key);
            if (list == null) {
                list = new ArrayList<>();
                bySource.put(key, list);
            }
            list.add(mc);
        }
    }

    String modOf(Class<?> cls) {
        String hit = cache.get(cls.getName());
        if (hit != null) return hit;
        String result = "unknown";
        try {
            CodeSource cs = cls.getProtectionDomain()
                .getCodeSource();
            List<ModContainer> candidates = cs == null ? null : bySource.get(key(fileOf(cs.getLocation())));
            if (candidates != null) result = best(candidates, packageOf(cls.getName()));
        } catch (Throwable ignored) {
            // "unknown" stays.
        }
        cache.put(cls.getName(), result);
        return result;
    }

    private static String best(List<ModContainer> candidates, String pkg) {
        if (candidates.size() == 1) return candidates.get(0)
            .getModId();
        ModContainer best = null;
        int bestLen = -1;
        for (ModContainer mc : candidates) {
            Object mod = mc.getMod();
            if (mod == null) continue;
            String modPkg = packageOf(
                mod.getClass()
                    .getName());
            if (!(pkg.equals(modPkg) || pkg.startsWith(modPkg + "."))) continue;
            // Longest package wins; a tie (gregtech and gregtech_nh both live in package gregtech) goes to the
            // smallest modid, so the result never depends on load order.
            if (modPkg.length() > bestLen || (modPkg.length() == bestLen && mc.getModId()
                .compareTo(best.getModId()) < 0)) {
                best = mc;
                bestLen = modPkg.length();
            }
        }
        return best == null ? candidates.get(0)
            .getModId() : best.getModId();
    }

    private static String packageOf(String className) {
        int i = className.lastIndexOf('.');
        return i < 0 ? "" : className.substring(0, i);
    }

    static File fileOf(URL url) {
        if (url == null) return null;
        String s = url.toString();
        if (s.startsWith("jar:")) {
            int bang = s.indexOf("!/");
            s = s.substring(4, bang < 0 ? s.length() : bang);
        }
        try {
            return new File(new java.net.URI(s));
        } catch (Throwable t) {
            return null;
        }
    }

    private static String key(File f) {
        if (f == null) return null;
        try {
            return f.getCanonicalPath();
        } catch (Throwable t) {
            return f.getAbsolutePath();
        }
    }
}
