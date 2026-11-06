package io.github.crazie450prog.gregteclink.listener;

import io.github.crazie450prog.gregteclink.GregtecLink;

/** Machine place/break listener (WO-6.2). Not built yet: registers nothing. */
public final class ListenerModule {

    private ListenerModule() {}

    public static void preInit() {
        GregtecLink.LOG.warn("listener module is enabled but not built yet (WO-6.2); it registers nothing");
    }
}
