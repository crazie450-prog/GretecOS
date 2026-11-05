package io.github.crazie450prog.gregteclink.client;

import io.github.crazie450prog.gregteclink.GregtecLink;

/** Client link (WO-8.1), client side only. Not built yet: registers nothing. */
public final class ClientModule {

    private ClientModule() {}

    public static void preInit() {
        GregtecLink.LOG.warn("client module is enabled but not built yet (WO-8.1); it registers nothing");
    }
}
