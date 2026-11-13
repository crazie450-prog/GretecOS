package io.github.crazie450prog.gregteclink.client;

import java.io.File;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;

import net.minecraft.client.Minecraft;
import net.minecraft.client.settings.KeyBinding;
import net.minecraft.entity.player.EntityPlayer;
import net.minecraft.util.ChatComponentText;
import net.minecraft.util.ChatComponentTranslation;
import net.minecraft.util.EnumChatFormatting;
import net.minecraft.util.IChatComponent;
import net.minecraftforge.client.event.ClientChatReceivedEvent;
import net.minecraftforge.common.MinecraftForge;

import org.lwjgl.input.Keyboard;
import org.lwjgl.input.Mouse;

import com.google.gson.JsonObject;

import cpw.mods.fml.client.registry.ClientRegistry;
import cpw.mods.fml.common.FMLCommonHandler;
import cpw.mods.fml.common.eventhandler.SubscribeEvent;
import cpw.mods.fml.common.gameevent.TickEvent;
import io.github.crazie450prog.gregteclink.GregtecLink;
import io.github.crazie450prog.gregteclink.Tags;

/**
 * The client link (WO-8.1, D285), client side only: a WebSocket to the core on 127.0.0.1 ({@link CoreLink}) with the
 * instance's token; position and dimension at 1 Hz; the hovered item (any GUI, NEI included) or the looked-at block on
 * the What's this? key, or when the core asks; chat lines; and GOS's notices as local chat lines, which only this
 * player sees. Each part has its toggle ({@link ClientConfig}). Registers a key binding and event handlers only: no
 * items, blocks, mixins, recipes, commands or packets.
 */
public final class ClientModule {

    public static final String KEY_CATEGORY = "key.categories.gregteclink";
    public static final String KEY_WHATS_THIS = "key.gregteclink.whatsthis";
    static final int RECENT_MAX = 64;

    private static ClientModule instance;

    public final ClientConfig cfg;
    public final CoreLink link;
    private final Feeds feeds;
    private final KeyBinding key;
    private boolean keyWasDown;
    /** The newest notices the core sent, as sent (for the harness). */
    private final Deque<JsonObject> recent = new ArrayDeque<>();

    private ClientModule(ClientConfig cfg, CoreLink link, KeyBinding key) {
        this.cfg = cfg;
        this.link = link;
        this.key = key;
        this.feeds = new Feeds(cfg, link::send, ClientModule::show);
    }

    public static void preInit(File gameDir, File configDir) {
        ClientConfig cfg = ClientConfig.load(configDir);
        GregtecLink.LOG.info("client module: {}", cfg);
        File tokenFile = cfg.tokenFile(gameDir);
        if (tokenFile == null) {
            GregtecLink.LOG.warn("client module: no token_file in the client category; it does nothing");
            return;
        }
        String player = Minecraft.getMinecraft()
            .getSession()
            .getUsername();
        CoreLink link = CoreLink.create(cfg.port, tokenFile, () -> LinkMessages.hello(player, Tags.VERSION, cfg));
        // Unbound by default: GTNH binds a great many keys; the player picks one in Controls (GregtecLink), best a key
        // that types nothing (F13-F24, Pause, a mouse button; D180), though typing into a text field doesn't fire it.
        KeyBinding key = new KeyBinding(KEY_WHATS_THIS, Keyboard.KEY_NONE, KEY_CATEGORY);
        ClientRegistry.registerKeyBinding(key);
        ClientModule m = new ClientModule(cfg, link, key);
        FMLCommonHandler.instance()
            .bus()
            .register(m);
        MinecraftForge.EVENT_BUS.register(m);
        instance = m;
        link.start();
    }

    /** The running module, or null when it is off. */
    public static ClientModule instance() {
        return instance;
    }

    @SubscribeEvent
    public void onClientTick(TickEvent.ClientTickEvent e) {
        if (e.phase != TickEvent.Phase.END) return;
        Minecraft mc = Minecraft.getMinecraft();
        JsonObject msg;
        while ((msg = link.poll()) != null) {
            String type = feeds.receive(msg, () -> Targets.current(mc));
            if ("notice".equals(type)) remember(msg);
        }
        feeds.tick(System.currentTimeMillis(), () -> status(mc));
        boolean down = keyDown(mc);
        if (down && !keyWasDown) whatsThis();
        keyWasDown = down;
    }

    /** What the key does: report what the player points at now. Returns the report's id, 0 if none was sent. */
    public long whatsThis() {
        Minecraft mc = Minecraft.getMinecraft();
        return feeds.whatsThis(() -> Targets.current(mc));
    }

    /**
     * Polled, not a key event: in a GUI the game hands keys to the screen, not to key bindings, and the hovered item
     * is only there while a GUI is open. A keyboard key doesn't count while the player types (chat, a sign, a focused
     * text field, NEI's search box: {@link Typing}).
     */
    private boolean keyDown(Minecraft mc) {
        int code = key.getKeyCode();
        if (code == 0) return false;
        try {
            if (code < 0) return Mouse.isButtonDown(code + 100);
            return Keyboard.isKeyDown(code) && !Typing.typing(mc.currentScreen);
        } catch (RuntimeException e) {
            return false;
        }
    }

    private static String status(Minecraft mc) {
        EntityPlayer p = mc.thePlayer;
        if (p == null || mc.theWorld == null) return null;
        return LinkMessages
            .status(p.dimension, mc.theWorld.provider.getDimensionName(), p.posX, p.posY, p.posZ, mc.isGamePaused());
    }

    @SubscribeEvent
    public void onChat(ClientChatReceivedEvent e) {
        IChatComponent m = e.message;
        if (m == null) return;
        String text = EnumChatFormatting.getTextWithoutFormattingCodes(m.getUnformattedText());
        String from = null, body = null;
        // A player's chat message is vanilla's chat.type.text with the name and the text as its arguments, so the
        // sender can't be faked by what someone types.
        if (m instanceof ChatComponentTranslation && "chat.type.text".equals(((ChatComponentTranslation) m).getKey())) {
            Object[] args = ((ChatComponentTranslation) m).getFormatArgs();
            if (args.length == 2) {
                from = plain(args[0]);
                body = plain(args[1]);
            }
        }
        // isSingleplayer: an integrated server runs here (single player, or the LAN host). On another server a chat
        // bridge or a team prefix could make a line whose sender reads as Clayton's (D286, [QA:V6]).
        feeds.chat(
            text,
            from,
            body,
            Minecraft.getMinecraft()
                .isSingleplayer());
    }

    private static String plain(Object arg) {
        String s = arg instanceof IChatComponent ? ((IChatComponent) arg).getUnformattedText() : String.valueOf(arg);
        return EnumChatFormatting.getTextWithoutFormattingCodes(s);
    }

    /** A local chat line: printed on this client only, never sent to the server. */
    private static void show(String line) {
        GregtecLink.LOG.info("notice: {}", line);
        Minecraft mc = Minecraft.getMinecraft();
        if (mc.ingameGUI != null && mc.theWorld != null) {
            mc.ingameGUI.getChatGUI()
                .printChatMessage(new ChatComponentText(line));
        }
    }

    private synchronized void remember(JsonObject msg) {
        recent.addLast(msg);
        while (recent.size() > RECENT_MAX) recent.removeFirst();
    }

    /** The newest notices from the core, oldest first. */
    public synchronized List<JsonObject> recentNotices() {
        return new ArrayList<>(recent);
    }
}
