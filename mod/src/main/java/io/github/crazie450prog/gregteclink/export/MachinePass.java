package io.github.crazie450prog.gregteclink.export;

import java.io.IOException;

import net.minecraft.block.Block;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import cpw.mods.fml.common.registry.GameData;
import gregtech.api.GregTechAPI;
import gregtech.api.interfaces.ISecondaryDescribable;
import gregtech.api.interfaces.metatileentity.IMetaTileEntity;
import gregtech.api.interfaces.tileentity.RecipeMapWorkable;
import gregtech.api.metatileentity.implementations.MTEMultiBlockBase;
import gregtech.api.metatileentity.implementations.MTETieredMachineBlock;
import gregtech.api.recipe.RecipeMap;

/**
 * (b) of plan §5.2 step 2: one line per non-null {@link GregTechAPI#METATILEENTITIES} entry. The key is the machine
 * block's registry name + meta; GT's mte_id equals that meta.
 */
final class MachinePass {

    private final ModSources mods;
    private final RefTable refs;
    int machines;
    int machineErrors;

    MachinePass(ModSources mods, RefTable refs) {
        this.mods = mods;
        this.refs = refs;
    }

    void write(HashedFile out) throws IOException {
        Block block = GregTechAPI.sBlockMachines;
        Object regName = GameData.getBlockRegistry()
            .getNameForObject(block);
        String blockName = regName == null ? null : regName.toString();
        net.minecraft.item.Item blockItem = net.minecraft.item.Item.getItemFromBlock(block);
        IMetaTileEntity[] all = GregTechAPI.METATILEENTITIES;
        for (int id = 0; id < all.length; id++) {
            IMetaTileEntity mte = all[id];
            if (mte == null) continue;
            machines++;
            JsonObject o = new JsonObject();
            JsonArray errors = new JsonArray();
            o.addProperty("mte_id", id);
            o.add("item", Json.str(blockName));
            o.addProperty("meta", id);
            if (blockName != null && blockItem != null) refs.item(blockName, id, blockItem);
            o.addProperty(
                "class",
                mte.getClass()
                    .getName());
            o.addProperty("mod", mods.modOf(mte.getClass()));
            try {
                o.addProperty("meta_name", mte.getMetaName());
            } catch (Throwable t) {
                errors.add(Json.str("meta_name: " + Json.error(t)));
            }
            try {
                o.addProperty("name", mte.getLocalName());
            } catch (Throwable t) {
                errors.add(Json.str("name: " + Json.error(t)));
            }
            if (mte instanceof MTETieredMachineBlock) {
                o.addProperty("tier", ((MTETieredMachineBlock) mte).mTier);
            }
            boolean multi = mte instanceof MTEMultiBlockBase;
            o.addProperty("multiblock", multi);
            if (mte instanceof RecipeMapWorkable) {
                RecipeMapWorkable w = (RecipeMapWorkable) mte;
                try {
                    RecipeMap<?> map = w.getRecipeMap();
                    if (map != null) o.addProperty("recipe_map", map.unlocalizedName);
                } catch (Throwable t) {
                    errors.add(Json.str("recipe_map: " + Json.error(t)));
                }
                try {
                    JsonArray maps = new JsonArray();
                    for (RecipeMap<?> m : w.getAvailableRecipeMaps()) {
                        if (m != null) maps.add(Json.str(m.unlocalizedName));
                    }
                    o.add("recipe_maps", maps);
                } catch (Throwable t) {
                    errors.add(Json.str("recipe_maps: " + Json.error(t)));
                }
            }
            if (multi) {
                MTEMultiBlockBase m = (MTEMultiBlockBase) mte;
                try {
                    JsonObject flags = new JsonObject();
                    flags.addProperty("batch_mode", m.supportsBatchMode());
                    flags.addProperty("input_separation", m.supportsInputSeparation());
                    flags.addProperty("void_protection", m.supportsVoidProtection());
                    flags.addProperty("recipe_locking", m.supportsSingleRecipeLocking());
                    o.add("flags", flags);
                } catch (Throwable t) {
                    errors.add(Json.str("flags: " + Json.error(t)));
                }
            }
            // Raw lines with § codes. Shift-dependent text can differ between client and server.
            if (mte instanceof ISecondaryDescribable) {
                ISecondaryDescribable d = (ISecondaryDescribable) mte;
                try {
                    o.add("tooltip", lines(d.getPrimaryDescription()));
                } catch (Throwable t) {
                    errors.add(Json.str("tooltip: " + Json.error(t)));
                }
                try {
                    o.add("structure_tooltip", lines(d.getSecondaryDescription()));
                } catch (Throwable t) {
                    errors.add(Json.str("structure_tooltip: " + Json.error(t)));
                }
            } else {
                try {
                    o.add("tooltip", lines(mte.getDescription()));
                } catch (Throwable t) {
                    errors.add(Json.str("tooltip: " + Json.error(t)));
                }
            }
            if (errors.size() > 0) {
                o.add("errors", errors);
                machineErrors++;
            }
            out.line(o);
        }
    }

    private static JsonArray lines(String[] text) {
        JsonArray a = new JsonArray();
        if (text != null) {
            for (String s : text) a.add(Json.str(s));
        }
        return a;
    }
}
