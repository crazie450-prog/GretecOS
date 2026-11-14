package io.github.crazie450prog.gregteclink.export;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;

import java.awt.image.BufferedImage;

import org.junit.jupiter.api.Test;

/** The palette pass's pure parts: icon name to PNG path, mod of a registry name, average colour (WO-10.3). */
class PalettePassTest {

    @Test
    void iconNamesResolveToTheBlockAtlasPath() {
        assertEquals(
            "assets/gregtech/textures/blocks/iconsets/MACHINE_LV_SIDE.png",
            PalettePass.texturePath("gregtech:iconsets/MACHINE_LV_SIDE"));
        // Vanilla icons have no domain; the domain is lower-cased as ResourceLocation does, the path is kept.
        assertEquals("assets/minecraft/textures/blocks/stone.png", PalettePass.texturePath("stone"));
        assertEquals(
            "assets/goodgenerator/textures/blocks/FRF_Coils/1.png",
            PalettePass.texturePath("GoodGenerator:FRF_Coils/1"));
    }

    @Test
    void modIsTheRegistryDomain() {
        assertEquals("gregtech", PalettePass.modOf("gregtech:gt.blockcasings"));
        assertEquals("minecraft", PalettePass.modOf("stone"));
    }

    @Test
    void averageIsAlphaWeightedOverTheFirstFrame() {
        // 2x4: an animation of two 2x2 frames; only the top frame counts.
        BufferedImage img = new BufferedImage(2, 4, BufferedImage.TYPE_INT_ARGB);
        img.setRGB(0, 0, 0xFFFF0000); // opaque red
        img.setRGB(1, 0, 0xFF0000FF); // opaque blue
        img.setRGB(0, 1, 0x00FFFFFF); // transparent white: no weight
        img.setRGB(1, 1, 0x00FFFFFF);
        img.setRGB(0, 2, 0xFF00FF00); // second frame: ignored
        img.setRGB(1, 3, 0xFF00FF00);
        assertArrayEquals(new int[] { 128, 0, 128, 128 }, PalettePass.average(img));
    }

    @Test
    void aFullyTransparentImageIsBlackWithNoAlpha() {
        BufferedImage img = new BufferedImage(2, 2, BufferedImage.TYPE_INT_ARGB);
        assertArrayEquals(new int[] { 0, 0, 0, 0 }, PalettePass.average(img));
    }
}
