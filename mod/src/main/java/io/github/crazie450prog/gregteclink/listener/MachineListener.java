package io.github.crazie450prog.gregteclink.listener;

import java.io.IOException;
import java.util.List;

import net.minecraft.block.Block;
import net.minecraft.tileentity.TileEntity;
import net.minecraft.world.ChunkPosition;
import net.minecraft.world.World;
import net.minecraft.world.WorldServer;
import net.minecraft.world.chunk.Chunk;
import net.minecraftforge.common.DimensionManager;
import net.minecraftforge.event.world.BlockEvent;

import cpw.mods.fml.common.eventhandler.EventPriority;
import cpw.mods.fml.common.eventhandler.SubscribeEvent;
import cpw.mods.fml.common.gameevent.TickEvent;
import cpw.mods.fml.common.registry.GameData;
import gregtech.api.GregTechAPI;
import gregtech.api.metatileentity.BaseMetaTileEntity;
import io.github.crazie450prog.gregteclink.GregtecLink;

/**
 * The server-side place/break listener (WO-6.2, plan 5.4, D70, V26, D260).
 *
 * <p>
 * A player placing GT's machine block ({@code PlaceEvent}) or breaking it ({@code BreakEvent}) is noted; at the end of
 * the server tick the world is read again and each real change is written to the {@link EventFile} as
 * {@code machine_built} or {@code machine_removed} with the machine's {@code mID} (GT's MetaTileEntity id, which is
 * also its item's meta) and the block's registry name. Handlers run at the lowest priority and never see cancelled
 * events, and the tick-end read confirms the change, so a placement or break another mod cancelled is not reported.
 *
 * <p>
 * <b>Read only.</b> The listener never changes the world: it reads a loaded chunk's tile-entity map directly (not
 * {@code World.getTileEntity}, which can create a missing tile entity) and never loads a chunk. It adds no items,
 * blocks, mixins, commands or packets. Machines placed or removed without a player event (explosions, other mods'
 * placers) are not seen here; GOS's tile-entity census of the saved chunks catches them.
 */
public final class MachineListener implements Pending.World {

    /** GT's tile entity for machines; cables and pipes are {@code BaseMetaPipeEntity} and are not counted. */
    private static final long WARN_EVERY_MS = 60_000;

    private final EventFile file;
    private final Pending pending = new Pending();
    private long lastWarn;
    private int written;

    MachineListener(EventFile file) {
        this.file = file;
    }

    int written() {
        return written;
    }

    @SubscribeEvent(priority = EventPriority.LOWEST)
    public void onPlace(BlockEvent.PlaceEvent e) {
        if (e.world.isRemote || e.placedBlock != GregTechAPI.sBlockMachines) return;
        pending.placed(e.world.provider.dimensionId, e.x, e.y, e.z);
    }

    @SubscribeEvent(priority = EventPriority.LOWEST)
    public void onBreak(BlockEvent.BreakEvent e) {
        if (e.world.isRemote || e.block != GregTechAPI.sBlockMachines) return;
        int dim = e.world.provider.dimensionId;
        pending.breaking(dim, e.x, e.y, e.z, mteAt(e.world, e.x, e.y, e.z), registryName(e.block));
    }

    @SubscribeEvent
    public void onServerTick(TickEvent.ServerTickEvent e) {
        if (e.phase != TickEvent.Phase.END || pending.isEmpty()) return;
        List<Pending.Event> events = pending.settle(this);
        int dropped = pending.takeDropped();
        if (dropped > 0) warn("dropped " + dropped + " place/break notes in one tick (over " + Pending.MAX + ")", null);
        long now = System.currentTimeMillis();
        for (Pending.Event ev : events) {
            try {
                file.write(ev, now);
                written++;
            } catch (IOException | RuntimeException t) {
                warn("event not written: " + ev, t);
            }
        }
    }

    private void warn(String what, Throwable t) {
        long now = System.currentTimeMillis();
        if (now - lastWarn < WARN_EVERY_MS) return;
        lastWarn = now;
        GregtecLink.LOG.warn("listener: {}{}", what, t == null ? "" : " (" + t + ")");
    }

    @Override
    public int mteAt(int dim, int x, int y, int z) {
        WorldServer world = DimensionManager.getWorld(dim);
        return world == null ? 0 : mteAt(world, x, y, z);
    }

    @Override
    public String blockAt(int dim, int x, int y, int z) {
        WorldServer world = DimensionManager.getWorld(dim);
        if (world == null || !loaded(world, x, y, z)) return null;
        return registryName(
            world.getChunkFromChunkCoords(x >> 4, z >> 4)
                .getBlock(x & 15, y, z & 15));
    }

    /** The mID of the GT machine at the position, 0 if none; reads a loaded chunk only, never creates anything. */
    public static int mteAt(World world, int x, int y, int z) {
        if (!loaded(world, x, y, z)) return 0;
        Chunk chunk = world.getChunkFromChunkCoords(x >> 4, z >> 4);
        TileEntity te = chunk.chunkTileEntityMap.get(new ChunkPosition(x & 15, y, z & 15));
        if (!(te instanceof BaseMetaTileEntity) || te.isInvalid()) return 0;
        int id = ((BaseMetaTileEntity) te).getMetaTileID();
        return id > 0 ? id : 0;
    }

    private static boolean loaded(World world, int x, int y, int z) {
        return y >= 0 && y < 256
            && world.getChunkProvider()
                .chunkExists(x >> 4, z >> 4);
    }

    static String registryName(Block block) {
        Object name = GameData.getBlockRegistry()
            .getNameForObject(block);
        return name == null ? null : name.toString();
    }
}
