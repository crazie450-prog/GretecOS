package io.github.crazie450prog.gregteclink.export;

import java.awt.image.BufferedImage;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;
import java.util.TreeSet;

import javax.imageio.ImageIO;
import javax.imageio.stream.MemoryCacheImageInputStream;

import net.minecraft.block.Block;
import net.minecraft.block.BlockFalling;
import net.minecraft.client.Minecraft;
import net.minecraft.creativetab.CreativeTabs;
import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraft.util.IIcon;
import net.minecraft.util.ResourceLocation;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import cpw.mods.fml.common.FMLCommonHandler;
import cpw.mods.fml.common.registry.GameData;
import io.github.crazie450prog.gregteclink.GregtecLink;

/**
 * The palette and texture pass (WO-10.3, plan 7.10 "Palette rule"): on the client, after the game has loaded its
 * textures, one line per placeable block state in {@code palette.jsonl} and one line per block texture it uses in
 * {@code textures.jsonl}. A block state is placeable when its block has an item ({@code Item.getItemFromBlock}); its
 * states are the item's variants as the creative menu lists them ({@code getSubItems}), each placed at the block meta
 * the item gives it ({@code Item.getMetadata(damage)}). Each line has the block's registry name and that meta, the
 * item that places it, the display name, Forge's {@code hasTileEntity(meta)} and what a shell needs to choose blocks
 * (opaque, full cube, light, falls, creative tab, mod), and the six faces' icon names. Keys are registry names and
 * metas only: no numeric block or item ID is ever written (EndlessIDs). Texture lines resolve each icon name to its
 * PNG in the loaded resource packs, as the block atlas does, with the image's size, sha256 and average colour (the
 * first frame of an animation); the PNG itself is not copied.
 */
final class PalettePass {

    static final String FILE = "palette.jsonl";
    static final String TEXTURES = "textures.jsonl";
    /** Sides in Minecraft's order: down, up, north, south, west, east. */
    static final int SIDES = 6;
    /** A texture bigger than this is not read (the average colour is then missing). */
    static final int MAX_TEXTURE_BYTES = 4 << 20;
    /** Forge names the icon of a block that registered none {@code MISSING_ICON_BLOCK_<numeric id>_<name>}. */
    static final String MISSING_ICON = "MISSING_ICON_";

    int blocks;
    int states;
    int tileEntities;
    int unplaceable;
    int hidden;
    int duplicates;
    int errors;
    int textures;
    int textureErrors;
    /** Every icon name a face uses, sorted. */
    private final TreeSet<String> icons = new TreeSet<>();

    /** Whether this side can run the pass (block icons and creative lists are client-only). */
    static boolean client() {
        try {
            return FMLCommonHandler.instance()
                .getSide()
                .isClient();
        } catch (Throwable t) {
            return false;
        }
    }

    /**
     * {@code parts.palette} for a run whose pass did not run: "skipped_server" on a dedicated server, else "failed"
     * with the reason recorded.
     */
    static String notRun(ExportRun run) {
        if (!client()) return "skipped_server";
        run.partErrors.put("palette", "not run: the client step did not reach it");
        return "failed";
    }

    /**
     * Runs the pass on the calling (client) thread and records its status, counts and duration on the run. Never
     * throws.
     */
    static void run(ExportRun run) {
        long t = System.currentTimeMillis();
        PalettePass pass = new PalettePass();
        try (HashedFile palette = run.newFile(FILE); HashedFile tex = run.newFile(TEXTURES)) {
            pass.write(palette, tex);
            run.palette = "ok";
        } catch (Throwable e) {
            GregtecLink.LOG.error("export: palette pass failed", e);
            run.palette = "failed";
            run.partErrors.put("palette", Json.error(e));
        }
        run.durations.put("palette", System.currentTimeMillis() - t);
        run.counts.put("palette_blocks", pass.blocks);
        run.counts.put("palette_states", pass.states);
        run.counts.put("palette_tile_entities", pass.tileEntities);
        run.counts.put("palette_unplaceable", pass.unplaceable);
        run.counts.put("palette_hidden", pass.hidden);
        run.counts.put("palette_duplicates", pass.duplicates);
        run.counts.put("palette_errors", pass.errors);
        run.counts.put("textures", pass.textures);
        run.counts.put("texture_errors", pass.textureErrors);
    }

    void write(HashedFile palette, HashedFile tex) throws IOException {
        TreeMap<String, Block> byName = new TreeMap<>();
        for (Object o : GameData.getBlockRegistry()) {
            Block b = (Block) o;
            Object name = GameData.getBlockRegistry()
                .getNameForObject(b);
            if (name != null) byName.put(name.toString(), b);
        }
        for (Map.Entry<String, Block> e : byName.entrySet()) {
            blocks++;
            for (JsonObject line : block(e.getKey(), e.getValue())) palette.line(line);
        }
        for (String icon : icons) {
            tex.line(texture(icon));
            textures++;
        }
    }

    /** The lines of one block, sorted by meta, then the placing item's meta. */
    private List<JsonObject> block(String name, Block block) {
        List<JsonObject> out = new ArrayList<>();
        Item item;
        try {
            item = Item.getItemFromBlock(block);
        } catch (Throwable t) {
            item = null;
        }
        if (item == null) {
            unplaceable++;
            return out;
        }
        List<ItemStack> subs = new ArrayList<>();
        boolean listed = true;
        try {
            CreativeTabs tab = block.getCreativeTabToDisplayOn();
            List<ItemStack> raw = new ArrayList<>();
            item.getSubItems(item, tab, raw);
            for (Object o : raw) {
                if (o instanceof ItemStack && ((ItemStack) o).getItem() == item) subs.add((ItemStack) o);
            }
        } catch (Throwable t) {
            JsonObject err = new JsonObject();
            err.addProperty("block", name);
            err.addProperty("error", "getSubItems: " + Json.error(t));
            out.add(err);
            errors++;
            subs.clear();
        }
        if (subs.isEmpty()) {
            // Not in any creative tab (or its list failed): the plain item still places the block.
            listed = false;
            subs.add(new ItemStack(item, 1, 0));
        }
        String itemName = StackJson.itemName(item);
        TreeMap<String, JsonObject> lines = new TreeMap<>();
        for (ItemStack stack : subs) {
            JsonObject line = new JsonObject();
            line.addProperty("block", name);
            int damage = stack.getItemDamage();
            try {
                int meta = item.getMetadata(damage);
                line.addProperty("meta", meta);
                JsonObject placer = new JsonObject();
                placer.add("item", Json.str(itemName));
                placer.addProperty("meta", damage);
                line.add("item", placer);
                fill(line, block, stack, meta);
                if (!listed) {
                    line.addProperty("hidden", true);
                    hidden++;
                }
                // Sorted by meta, then the item's meta, as unsigned numbers (10 digits each).
                String key = String
                    .format("%010d %010d", meta - (long) Integer.MIN_VALUE, damage - (long) Integer.MIN_VALUE);
                if (lines.containsKey(key)) {
                    duplicates++;
                    continue;
                }
                lines.put(key, line);
            } catch (Throwable t) {
                JsonObject err = new JsonObject();
                err.addProperty("block", name);
                JsonObject placer = new JsonObject();
                placer.add("item", Json.str(itemName));
                placer.addProperty("meta", damage);
                err.add("item", placer);
                err.addProperty("error", Json.error(t));
                out.add(err);
                errors++;
            }
        }
        for (JsonObject line : lines.values()) {
            states++;
            if (line.get("has_te")
                .getAsBoolean()) tileEntities++;
            out.add(line);
        }
        return out;
    }

    /** Name, flags and faces of one state; throws if the block can't say whether it has a tile entity. */
    private void fill(JsonObject line, Block block, ItemStack stack, int meta) {
        line.add("name", Json.str(safeName(stack)));
        line.add("unlocalized", Json.str(safeUnlocalized(block)));
        line.addProperty(
            "mod",
            modOf(
                line.get("block")
                    .getAsString()));
        line.addProperty("has_te", block.hasTileEntity(meta));
        List<String> errs = new ArrayList<>();
        flag(line, errs, "opaque", () -> block.isOpaqueCube());
        flag(line, errs, "full_cube", () -> block.renderAsNormalBlock());
        flag(line, errs, "normal_cube", () -> block.isNormalCube());
        flag(line, errs, "standard_render", () -> block.getRenderType() == 0);
        flag(
            line,
            errs,
            "liquid",
            () -> block.getMaterial()
                .isLiquid());
        line.addProperty("falls", block instanceof BlockFalling);
        try {
            line.addProperty("light", block.getLightValue());
        } catch (Throwable t) {
            line.add("light", JsonNull.INSTANCE);
            errs.add("light: " + Json.error(t));
        }
        try {
            line.addProperty("light_opacity", block.getLightOpacity());
        } catch (Throwable t) {
            line.add("light_opacity", JsonNull.INSTANCE);
            errs.add("light_opacity: " + Json.error(t));
        }
        try {
            CreativeTabs tab = block.getCreativeTabToDisplayOn();
            line.add("tab", Json.str(tab == null ? null : tab.getTabLabel()));
        } catch (Throwable t) {
            line.add("tab", JsonNull.INSTANCE);
        }
        JsonArray faces = new JsonArray();
        for (int side = 0; side < SIDES; side++) {
            String icon = null;
            try {
                IIcon i = block.getIcon(side, meta);
                icon = i == null ? null : i.getIconName();
                if (icon != null && icon.startsWith(MISSING_ICON)) {
                    // Forge's placeholder for a block with no icon; its name holds the numeric block id.
                    errs.add("face " + side + ": no icon");
                    icon = null;
                }
            } catch (Throwable t) {
                errs.add("face " + side + ": " + Json.error(t));
            }
            faces.add(Json.str(icon));
            if (icon != null) icons.add(icon);
        }
        line.add("faces", faces);
        if (!errs.isEmpty()) line.add("errors", Json.strings(errs));
    }

    private interface Flag {

        boolean get();
    }

    private static void flag(JsonObject line, List<String> errs, String key, Flag f) {
        try {
            line.addProperty(key, f.get());
        } catch (Throwable t) {
            line.add(key, JsonNull.INSTANCE);
            errs.add(key + ": " + Json.error(t));
        }
    }

    private static String safeName(ItemStack stack) {
        try {
            return stack.getDisplayName();
        } catch (Throwable t) {
            return null;
        }
    }

    private static String safeUnlocalized(Block block) {
        try {
            return block.getUnlocalizedName();
        } catch (Throwable t) {
            return null;
        }
    }

    /** The registry name's domain: the mod that registered the block. */
    static String modOf(String registryName) {
        int colon = registryName.indexOf(':');
        return colon < 0 ? "minecraft" : registryName.substring(0, colon);
    }

    /** The PNG an icon name stands for in the block atlas: {@code domain:path} to {@code textures/blocks/path.png}. */
    static String texturePath(String icon) {
        String domain = "minecraft";
        String path = icon;
        int colon = icon.indexOf(':');
        if (colon >= 0) {
            domain = icon.substring(0, colon);
            path = icon.substring(colon + 1);
        }
        return "assets/" + domain.toLowerCase(Locale.ROOT) + "/textures/blocks/" + path + ".png";
    }

    /** {icon, path, width?, height?, sha256?, rgb?, alpha?, error?} for one icon name. */
    private JsonObject texture(String icon) {
        JsonObject o = new JsonObject();
        o.addProperty("icon", icon);
        String path = texturePath(icon);
        o.addProperty("path", path);
        try {
            String rest = path.substring("assets/".length());
            int slash = rest.indexOf('/');
            ResourceLocation rl = new ResourceLocation(rest.substring(0, slash), rest.substring(slash + 1));
            byte[] bytes;
            try (InputStream in = Minecraft.getMinecraft()
                .getResourceManager()
                .getResource(rl)
                .getInputStream()) {
                bytes = readAll(in);
            }
            MessageDigest d = MessageDigest.getInstance("SHA-256");
            o.addProperty("sha256", Json.hex(d.digest(bytes)));
            BufferedImage img;
            // In memory, so ImageIO writes no cache file; ImageIO.read closes the stream itself.
            img = ImageIO.read(new MemoryCacheImageInputStream(new ByteArrayInputStream(bytes)));
            if (img == null) throw new IOException("not an image ImageIO reads");
            o.addProperty("width", img.getWidth());
            o.addProperty("height", img.getHeight());
            int[] avg = average(img);
            o.addProperty("rgb", String.format("#%02x%02x%02x", avg[0], avg[1], avg[2]));
            o.addProperty("alpha", avg[3]);
        } catch (Throwable t) {
            o.addProperty("error", Json.error(t));
            textureErrors++;
        }
        return o;
    }

    private static byte[] readAll(InputStream in) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[1 << 14];
        int n;
        while ((n = in.read(buf)) > 0) {
            out.write(buf, 0, n);
            if (out.size() > MAX_TEXTURE_BYTES) throw new IOException("over " + MAX_TEXTURE_BYTES + " bytes");
        }
        return out.toByteArray();
    }

    /**
     * {r, g, b, alpha}, 0..255: the colour of the image's first frame (its top square: an animated texture stacks its
     * frames), averaged over the pixels weighted by their alpha, and the mean alpha. A fully transparent image is
     * black with alpha 0.
     */
    static int[] average(BufferedImage img) {
        int w = img.getWidth();
        int h = Math.min(img.getHeight(), w);
        long r = 0, g = 0, b = 0, a = 0;
        for (int y = 0; y < h; y++) {
            for (int x = 0; x < w; x++) {
                int argb = img.getRGB(x, y);
                int alpha = (argb >>> 24) & 0xFF;
                r += (long) ((argb >> 16) & 0xFF) * alpha;
                g += (long) ((argb >> 8) & 0xFF) * alpha;
                b += (long) (argb & 0xFF) * alpha;
                a += alpha;
            }
        }
        int n = w * h;
        if (a == 0) return new int[] { 0, 0, 0, 0 };
        return new int[] { (int) Math.round((double) r / a), (int) Math.round((double) g / a),
            (int) Math.round((double) b / a), (int) Math.round((double) a / n) };
    }
}
