package io.github.crazie450prog.gregteclink.export;

import static org.junit.jupiter.api.Assertions.assertEquals;

import net.minecraft.nbt.NBTTagByte;
import net.minecraft.nbt.NBTTagByteArray;
import net.minecraft.nbt.NBTTagCompound;
import net.minecraft.nbt.NBTTagDouble;
import net.minecraft.nbt.NBTTagFloat;
import net.minecraft.nbt.NBTTagIntArray;
import net.minecraft.nbt.NBTTagList;
import net.minecraft.nbt.NBTTagLong;
import net.minecraft.nbt.NBTTagShort;
import net.minecraft.nbt.NBTTagString;

import org.junit.jupiter.api.Test;

/** Canonical NBT JSON follows NEI's NBTJson typing, with sorted keys. */
class StackJsonTest {

    private static String json(net.minecraft.nbt.NBTBase tag) {
        return Json.COMPACT.toJson(StackJson.nbt(tag));
    }

    @Test
    void typedScalarsAndSortedKeys() {
        NBTTagCompound c = new NBTTagCompound();
        c.setTag("z", new NBTTagString("text"));
        c.setInteger("i", 7);
        c.setTag("b", new NBTTagByte((byte) 1));
        c.setTag("s", new NBTTagShort((short) -2));
        c.setTag("l", new NBTTagLong(3L));
        c.setTag("f", new NBTTagFloat(1.5f));
        c.setTag("d", new NBTTagDouble(2.25d));
        assertEquals(
            "{\"b\":\"1B\",\"d\":\"2.25D\",\"f\":\"1.5F\",\"i\":7,\"l\":\"3L\",\"s\":\"-2S\",\"z\":\"text\"}",
            json(c));
    }

    @Test
    void sameContentInAnyInsertionOrderGivesTheSameJson() {
        NBTTagCompound a = new NBTTagCompound();
        a.setInteger("x", 1);
        a.setString("y", "2");
        NBTTagCompound b = new NBTTagCompound();
        b.setString("y", "2");
        b.setInteger("x", 1);
        assertEquals(json(a), json(b));
    }

    @Test
    void listsAndArrays() {
        NBTTagCompound c = new NBTTagCompound();
        NBTTagList list = new NBTTagList();
        NBTTagCompound e1 = new NBTTagCompound();
        e1.setInteger("n", 1);
        list.appendTag(e1);
        NBTTagCompound e2 = new NBTTagCompound();
        e2.setInteger("n", 2);
        list.appendTag(e2);
        c.setTag("list", list);
        c.setTag("ints", new NBTTagIntArray(new int[] { 4, 5 }));
        c.setTag("bytes", new NBTTagByteArray(new byte[] { 6 }));
        assertEquals("{\"bytes\":[\"6B\"],\"ints\":[4,5],\"list\":[{\"n\":1},{\"n\":2}]}", json(c));
        // Draining a copy must leave the original list intact.
        assertEquals(2, list.tagCount());
    }

    @Test
    void savedItemStacksGetTheirRegistryName() {
        NBTTagCompound out = new NBTTagCompound();
        out.setShort("id", (short) 555);
        out.setByte("Count", (byte) 1);
        out.setShort("Damage", (short) 12741);
        NBTTagCompound unknown = new NBTTagCompound();
        unknown.setInteger("id", 40000);
        unknown.setByte("Count", (byte) 2);
        unknown.setShort("Damage", (short) 0);
        NBTTagCompound c = new NBTTagCompound();
        c.setTag("output", out);
        c.setTag("other", unknown);
        c.setShort("id", (short) 7);
        assertEquals(
            "{\"id\":\"7S\",\"other\":{\"Count\":\"2B\",\"Damage\":\"0S\",\"__item\":null,\"id\":40000},"
                + "\"output\":{\"Count\":\"1B\",\"Damage\":\"12741S\",\"__item\":\"gregtech:gt.metaitem.01\","
                + "\"id\":\"555S\"}}",
            Json.COMPACT.toJson(StackJson.nbt(c, id -> id == 555 ? "gregtech:gt.metaitem.01" : null)));
    }

    @Test
    void identityHashesAreStrippedAndItemStackStringsKept() {
        assertEquals(
            "java.lang.Object[a=1,b=java.util.ArrayList]",
            Json.stripIdentityHashes("java.lang.Object@1b6d3586[a=1,b=java.util.ArrayList@7f31245a]"));
        assertEquals(
            "(2xgt.metaitem.01@2097,1) 1xitem.photomask.ilc@0 no.such.Clazz@1a",
            Json.stripIdentityHashes("(2xgt.metaitem.01@2097,1) 1xitem.photomask.ilc@0 no.such.Clazz@1a"));
        assertEquals("java.lang.Object@123456789", Json.stripIdentityHashes("java.lang.Object@123456789"));
    }

    @Test
    void emptyListsKeepTheirType() {
        NBTTagCompound c = new NBTTagCompound();
        c.setTag("l", new NBTTagList());
        c.setTag("a", new NBTTagIntArray(new int[0]));
        assertEquals(
            "{\"a\":{\"__custom_type\":\"net.minecraft.nbt.NBTTagIntArray\"},"
                + "\"l\":{\"__custom_type\":\"net.minecraft.nbt.NBTTagList\"}}",
            json(c));
    }
}
