package io.github.crazie450prog.gregteclink.export;

import java.util.List;
import java.util.TreeMap;
import java.util.TreeSet;

import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraftforge.oredict.OreDictionary;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import gregtech.api.GregTechAPI;
import gregtech.api.interfaces.metatileentity.IMetaTileEntity;
import gregtech.api.metatileentity.BaseMetaTileEntity;
import gregtech.api.metatileentity.MetaTileEntity;
import gregtech.api.metatileentity.implementations.MTEBasicBatteryBuffer;
import gregtech.api.metatileentity.implementations.MTETransformer;
import ic2.api.item.ElectricItem;
import ic2.api.item.IElectricItem;
import kekztech.common.tileentities.MTELapotronicSuperCapacitor;

/**
 * Energy storage and distribution data (WO-4.5, D240), the {@code storage} object of gt_power.json: what the
 * distribution checks size buffers and transformers with. Cables need nothing here: their voltage, amperage and loss
 * are in their tooltips (gt_machines.jsonl).
 * <ul>
 * <li>{@code batteries}: every member of an oredict name starting with {@code battery} whose item is an IC2
 * {@link IElectricItem}: its tier, max charge, transfer limit and whether it takes a charge (simulated) as the item
 * gives them for that stack (GT's battery
 * buffer takes an item whose tier equals its own, {@code GTModHandler.isElectricItem(stack, tier)}).</li>
 * <li>{@code buffers}: every {@link MTEBasicBatteryBuffer}: tier, slots, the voltage in and out, its own EU store.</li>
 * <li>{@code transformers}: every {@link MTETransformer} (GT's, the power transformers, GT++'s hi-amp ones), read
 * from a {@link BaseMetaTileEntity} holding a new instance, in both modes: {@code down} (allowed to work, GT's default:
 * in at the tier above, out at its own) and {@code up} (after {@code disableWorking}). Half mode (a screwdriver) is
 * not read.</li>
 * <li>{@code lsc}: kekztech's Lapotronic Supercapacitor cells ({@code MTELapotronicSuperCapacitor.Capacitor}), each
 * with its block (the LSC part block at meta = the cell's index in {@code Capacitor.VALUES} + 1, as the LSC reads a
 * cell from a stack), the glass tier it needs and its capacity (a decimal string: a BigInteger).</li>
 * </ul>
 */
final class StoragePass {

    private final StackJson stacks;
    int errors;

    StoragePass(StackJson stacks) {
        this.stacks = stacks;
    }

    JsonObject json() {
        JsonObject o = new JsonObject();
        o.add("batteries", batteries());
        o.add("buffers", buffers());
        o.add("transformers", transformers());
        o.add("lsc", lsc());
        return o;
    }

    private JsonArray batteries() {
        TreeSet<String> names = new TreeSet<>();
        for (String name : OreDictionary.getOreNames()) {
            if (name != null && name.startsWith("battery")) names.add(name);
        }
        // One row per item ref, with every battery oredict name it is in, ordered by the ref's JSON.
        TreeMap<String, JsonObject> rows = new TreeMap<>();
        for (String name : names) {
            List<ItemStack> members = OreDictionary.getOres(name);
            for (ItemStack s : members) {
                if (s == null || !(s.getItem() instanceof IElectricItem)) continue;
                ItemStack one = s.copy();
                one.stackSize = 1;
                JsonObject ref = stacks.item(one)
                    .getAsJsonObject();
                ref.remove("n");
                String key = ref.toString();
                JsonObject row = rows.get(key);
                if (row == null) {
                    row = new JsonObject();
                    row.add("battery", ref);
                    row.add("ore", new JsonArray());
                    try {
                        IElectricItem e = (IElectricItem) one.getItem();
                        row.addProperty("tier", e.getTier(one));
                        row.addProperty("max_charge", e.getMaxCharge(one));
                        row.addProperty("transfer_limit", e.getTransferLimit(one));
                        // What a buffer can put back in it, simulated on a copy (a one-use item, GT's ZPM module,
                        // takes nothing).
                        row.addProperty(
                            "rechargeable",
                            ElectricItem.manager.charge(one.copy(), e.getMaxCharge(one), e.getTier(one), true, true)
                                > 0);
                    } catch (Throwable t) {
                        row.addProperty("error", Json.error(t));
                        errors++;
                    }
                    rows.put(key, row);
                }
                row.getAsJsonArray("ore")
                    .add(Json.str(name));
            }
        }
        JsonArray a = new JsonArray();
        for (JsonObject row : rows.values()) a.add(row);
        return a;
    }

    private JsonArray buffers() {
        JsonArray a = new JsonArray();
        IMetaTileEntity[] all = GregTechAPI.METATILEENTITIES;
        for (int id = 0; id < all.length; id++) {
            if (!(all[id] instanceof MTEBasicBatteryBuffer)) continue;
            MTEBasicBatteryBuffer b = (MTEBasicBatteryBuffer) all[id];
            JsonObject o = new JsonObject();
            o.addProperty("mte_id", id);
            o.addProperty(
                "class",
                b.getClass()
                    .getName());
            try {
                o.addProperty("tier", b.mTier);
                o.addProperty("slots", b.getSizeInventory());
                o.addProperty("eu_in", b.maxEUInput());
                o.addProperty("eu_out", b.maxEUOutput());
                o.addProperty("eu_store", b.maxEUStore());
            } catch (Throwable t) {
                o.addProperty("error", Json.error(t));
                errors++;
            }
            a.add(o);
        }
        return a;
    }

    private JsonArray transformers() {
        JsonArray a = new JsonArray();
        IMetaTileEntity[] all = GregTechAPI.METATILEENTITIES;
        for (int id = 0; id < all.length; id++) {
            if (!(all[id] instanceof MTETransformer)) continue;
            JsonObject o = new JsonObject();
            o.addProperty("mte_id", id);
            o.addProperty(
                "class",
                all[id].getClass()
                    .getName());
            try {
                o.addProperty("tier", ((MTETransformer) all[id]).mTier);
                BaseMetaTileEntity base = new BaseMetaTileEntity();
                base.setInitialValuesAsNBT(null, (short) id);
                MetaTileEntity t = (MetaTileEntity) base.getMetaTileEntity();
                o.add("down", mode(t));
                base.disableWorking();
                o.add("up", mode(t));
            } catch (Throwable e) {
                o.addProperty("error", Json.error(e));
                errors++;
            }
            a.add(o);
        }
        return a;
    }

    private static JsonObject mode(MetaTileEntity t) {
        JsonObject m = new JsonObject();
        m.addProperty("eu_in", t.maxEUInput());
        m.addProperty("amps_in", t.maxAmperesIn());
        m.addProperty("eu_out", t.maxEUOutput());
        m.addProperty("amps_out", t.maxAmperesOut());
        return m;
    }

    private JsonArray lsc() {
        JsonArray a = new JsonArray();
        Item part;
        try {
            java.lang.reflect.Field f = MTELapotronicSuperCapacitor.class.getDeclaredField("LSC_PART_ITEM");
            f.setAccessible(true);
            part = (Item) f.get(null);
        } catch (Throwable t) {
            JsonObject o = new JsonObject();
            o.addProperty("error", "LSC_PART_ITEM: " + Json.error(t));
            a.add(o);
            errors++;
            return a;
        }
        MTELapotronicSuperCapacitor.Capacitor[] values = MTELapotronicSuperCapacitor.Capacitor.VALUES;
        for (int i = 0; i < values.length; i++) {
            MTELapotronicSuperCapacitor.Capacitor c = values[i];
            JsonObject o = new JsonObject();
            o.addProperty("name", c.name());
            try {
                JsonObject ref = stacks.item(new ItemStack(part, 1, i + 1))
                    .getAsJsonObject();
                ref.remove("n");
                o.add("block", ref);
                o.addProperty("glass_tier", c.getMinimalGlassTier());
                o.addProperty(
                    "capacity",
                    c.getProvidedCapacity()
                        .toString());
            } catch (Throwable t) {
                o.addProperty("error", Json.error(t));
                errors++;
            }
            a.add(o);
        }
        return a;
    }
}
