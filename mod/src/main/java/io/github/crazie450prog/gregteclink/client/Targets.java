package io.github.crazie450prog.gregteclink.client;

import java.lang.reflect.Field;
import java.lang.reflect.Method;

import net.minecraft.block.Block;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.ScaledResolution;
import net.minecraft.client.gui.inventory.GuiContainer;
import net.minecraft.inventory.Slot;
import net.minecraft.item.ItemStack;
import net.minecraft.util.EnumChatFormatting;
import net.minecraft.util.MovingObjectPosition;
import net.minecraft.world.World;

import org.lwjgl.input.Mouse;

import cpw.mods.fml.common.registry.GameData;
import io.github.crazie450prog.gregteclink.GregtecLink;

/**
 * What the player points at: in a GUI, the item under the mouse (NEI's item panel and recipe pages included); in the
 * world, the block looked at, with the item middle-click would give for it ({@code getPickBlock}: a GT machine's item
 * carries its meta tile entity id). Registry names and meta only, never numeric IDs.
 */
public final class Targets {

    private static Method neiMouseOver;
    private static boolean neiLooked;
    private static Field guiLeft, guiTop;

    private Targets() {}

    public static Feeds.Target current(Minecraft mc) {
        try {
            if (mc.currentScreen instanceof GuiContainer) return hovered(mc, (GuiContainer) mc.currentScreen);
            if (mc.currentScreen == null) return lookedAt(mc);
        } catch (RuntimeException | LinkageError e) {
            GregtecLink.LOG.warn("client link: target not read: {}", e.toString());
        }
        return Feeds.Target.NONE;
    }

    /** The stack under the mouse: NEI's view first (its panels, then the slot), else the container's slot. */
    static Feeds.Target hovered(Minecraft mc, GuiContainer gui) {
        ItemStack stack = neiStackMouseOver(gui);
        if (stack == null) stack = slotStackMouseOver(mc, gui);
        return stack == null ? Feeds.Target.NONE
            : new Feeds.Target("item", LinkMessages.itemRef(stack), null, displayName(stack));
    }

    private static ItemStack neiStackMouseOver(GuiContainer gui) {
        if (!neiLooked) {
            neiLooked = true;
            try {
                neiMouseOver = Class.forName("codechicken.nei.guihook.GuiContainerManager")
                    .getMethod("getStackMouseOver", GuiContainer.class);
            } catch (ReflectiveOperationException | LinkageError e) {
                neiMouseOver = null; // no NEI: the slot alone
            }
        }
        if (neiMouseOver == null) return null;
        try {
            return (ItemStack) neiMouseOver.invoke(null, gui);
        } catch (ReflectiveOperationException | RuntimeException e) {
            return null;
        }
    }

    private static ItemStack slotStackMouseOver(Minecraft mc, GuiContainer gui) {
        try {
            if (guiLeft == null) {
                guiLeft = field("guiLeft", "field_147003_i");
                guiTop = field("guiTop", "field_147009_r");
            }
            ScaledResolution sr = new ScaledResolution(mc, mc.displayWidth, mc.displayHeight);
            int mx = Mouse.getX() * sr.getScaledWidth() / mc.displayWidth;
            int my = sr.getScaledHeight() - Mouse.getY() * sr.getScaledHeight() / mc.displayHeight - 1;
            int left = guiLeft.getInt(gui), top = guiTop.getInt(gui);
            for (Object o : gui.inventorySlots.inventorySlots) {
                Slot s = (Slot) o;
                int x = left + s.xDisplayPosition, y = top + s.yDisplayPosition;
                if (mx >= x - 1 && mx < x + 17 && my >= y - 1 && my < y + 17) return s.getStack();
            }
        } catch (ReflectiveOperationException | RuntimeException e) {
            GregtecLink.LOG.debug("client link: slot not read: {}", e.toString());
        }
        return null;
    }

    private static Field field(String deobf, String srg) throws NoSuchFieldException {
        Field f;
        try {
            f = GuiContainer.class.getDeclaredField(deobf);
        } catch (NoSuchFieldException e) {
            f = GuiContainer.class.getDeclaredField(srg);
        }
        f.setAccessible(true);
        return f;
    }

    /** The block the crosshair is on, and its pick item. */
    static Feeds.Target lookedAt(Minecraft mc) {
        MovingObjectPosition hit = mc.objectMouseOver;
        World world = mc.theWorld;
        if (hit == null || world == null
            || mc.thePlayer == null
            || hit.typeOfHit != MovingObjectPosition.MovingObjectType.BLOCK) {
            return Feeds.Target.NONE;
        }
        Block block = world.getBlock(hit.blockX, hit.blockY, hit.blockZ);
        Object name = GameData.getBlockRegistry()
            .getNameForObject(block);
        if (name == null) return Feeds.Target.NONE;
        int dim = world.provider.dimensionId;
        ItemStack pick = block.getPickBlock(hit, world, hit.blockX, hit.blockY, hit.blockZ, mc.thePlayer);
        return new Feeds.Target(
            "block",
            LinkMessages.itemRef(pick),
            LinkMessages.blockRef(
                name.toString(),
                world.getBlockMetadata(hit.blockX, hit.blockY, hit.blockZ),
                hit.blockX,
                hit.blockY,
                hit.blockZ,
                dim),
            pick == null ? null : displayName(pick));
    }

    static String displayName(ItemStack stack) {
        try {
            return EnumChatFormatting.getTextWithoutFormattingCodes(stack.getDisplayName());
        } catch (RuntimeException e) {
            return null;
        }
    }
}
