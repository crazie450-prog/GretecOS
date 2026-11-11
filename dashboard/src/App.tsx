// The shell (plan 7.14): header, left nav with number keys, the view, the always-there right
// rail and the voice strip in workbench mode; large type and the core art in glance mode; the
// Ctrl+K palette; the phone layout. Built from the design system only (D92, D158).
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, act as runAction, api } from "./api/client";
import { connect } from "./api/connection";
import type { CreditChoice } from "./api/types";
import { Card } from "./cards/Card";
import { CardContext, type CardActions } from "./cards/context";
import type { ApprovalCard, Card as CardData } from "./cards/types.gen";
import { Banners } from "./components/Banners";
import { CommandPalette } from "./components/CommandPalette";
import { Header } from "./components/Header";
import type { Action } from "./components/palette";
import { NavItem } from "./components/core";
import { Rail } from "./components/Rail";
import { VoiceStrip, voiceLook } from "./components/VoiceStrip";
import { type ModePin, modeFor } from "./state/mode";
import { bannersFor } from "./state/banners";
import { useMedia, usePresence } from "./state/presence";
import { isStale, stateChips } from "./state/states";
import { store, useApp } from "./state/store";
import { useTheme } from "./theme/useTheme";
import { Base } from "./views/Base";
import { Chat } from "./views/Chat";
import { Costs } from "./views/Costs";
import { Glance } from "./views/Glance";
import { Later } from "./views/Later";
import { PHONE_TABS, VIEWS, type ViewId, viewForKey, viewFromHash, viewOf } from "./views/nav";
import { Now } from "./views/Now";
import { Pairing } from "./views/Pairing";
import { Search, type SearchTarget } from "./views/Search";
import { type DoctorRun, Settings, runDoctor } from "./views/Settings";

const PIN_KEY = "gos.mode";

function loadPin(): ModePin {
  try {
    const v = window.localStorage.getItem(PIN_KEY);
    return v === "glance" || v === "workbench" ? v : "auto";
  } catch {
    return "auto";
  }
}

function typing(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
}

function useSeconds(everyMs: number): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), everyMs);
    return () => clearInterval(t);
  }, [everyMs]);
  return now;
}

export function App() {
  const phase = useApp((s) => s.phase);
  const reachable = useApp((s) => s.reachable);
  const core = useApp((s) => s.core);
  const answers = useApp((s) => s.answers);
  const deep = useApp((s) => s.deep);
  const session = useApp((s) => s.session);
  const socket = useApp((s) => s.socket);
  const voice = useApp((s) => s.voice);
  const nextCard = useApp((s) => s.nextCard);
  const [boot, setBoot] = useState(0);

  useEffect(() => {
    const c = connect(store);
    return () => c.stop();
  }, [boot]);

  const themeCtl = useTheme(core?.tier?.stage);
  const phone = useMedia("(max-width: 760px)");
  const presence = usePresence(core?.game.focused ?? null);
  const [pin, setPinState] = useState<ModePin>(loadPin);
  const setPin = useCallback((p: ModePin): void => {
    setPinState(p);
    try {
      window.localStorage.setItem(PIN_KEY, p);
    } catch {
      // the choice lasts until reload
    }
  }, []);
  const mode = modeFor({
    phone,
    pin,
    pointerInside: presence.pointerInside,
    msSinceLeave: presence.msSinceLeave,
    windowFocused: presence.windowFocused,
    gameRunning: core?.game.running ?? null,
    gameFocused: core?.game.focused ?? null,
    gamePaused: core?.game.paused ?? null,
  });
  const glance = mode === "glance";
  // Glance has no nav and no Settings, so a held glance (Settings) ends when he clicks a glance
  // block, presses Esc or uses the header's button: each goes back to automatic (D161).
  const held = glance && pin === "glance";

  const [view, setViewState] = useState<ViewId>(() => viewFromHash(window.location.hash));
  const setView = useCallback((v: ViewId) => {
    setViewState(v);
    if (window.location.hash !== `#/${v}`) window.location.hash = `/${v}`;
  }, []);
  useEffect(() => {
    const on = (): void => setViewState(viewFromHash(window.location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  const [palette, setPalette] = useState(false);
  const [modal, setModal] = useState<CardData | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [searchTarget, setSearchTarget] = useState<SearchTarget | null>(null);
  const [doctor, setDoctor] = useState<DoctorRun>({ running: false });
  const doctorNow = useCallback(() => void runDoctor(setDoctor), []);
  const nowS = useSeconds(15000);

  // Keyboard: number keys switch views and Ctrl+K opens the palette (workbench); Esc closes, and
  // ends a held glance.
  useEffect(() => {
    const on = (e: KeyboardEvent): void => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (!glance) setPalette(true);
        return;
      }
      if (e.key === "Escape") {
        setPalette(false);
        setModal(null);
        if (held) setPin("auto");
        return;
      }
      if (glance || palette || modal || e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return;
      const v = viewForKey(e.key);
      if (v) {
        e.preventDefault();
        setView(v.id);
      }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [glance, held, palette, modal, setView, setPin]);

  const show = (e: unknown): void => setNotice(e instanceof ApiError ? e.message : String(e));

  const ask = useCallback(
    async (text: string): Promise<void> => {
      try {
        await api.ask(text);
        if (store.get().socket !== "open") store.set({ answers: await api.answers() });
      } catch (e) {
        show(e);
      }
    },
    [],
  );

  const act = useCallback((card: ApprovalCard, action: string) => {
    runAction(card, action).catch(show);
  }, []);

  // The credit alert's answer and the AI switch (WO-2.8); the core pushes the new state.
  const decide = useCallback(async (choice: CreditChoice): Promise<void> => {
    try {
      await api.creditDecision(choice);
    } catch (e) {
      show(e);
    }
  }, []);
  const turnOn = useCallback(async (): Promise<void> => {
    try {
      await api.aiSwitch(true);
    } catch (e) {
      show(e);
    }
  }, []);

  const actions: CardActions = useMemo(
    () => ({
      stale: isStale(core),
      openCite: (cite: string) => {
        api.recipe(cite).then(setModal, show);
      },
      openQuest: (ref: string, spoil: number) => {
        api.tool("quest_get", { id: ref.replace(/^q:/, ""), spoil }).then((r) => setModal(r.card), show);
      },
      act,
      pick: (n: number) => void ask(String(n)),
    }),
    [core, act, ask],
  );

  const openFromGlance = (v: ViewId): void => {
    if (held) setPin("auto");
    setView(v);
  };

  const runPalette = (a: Action): void => {
    if (a.kind === "view") setView(a.view);
    else if (a.kind === "item") {
      setSearchTarget({ kind: "item", key: a.key, name: a.name, tool: a.tool });
      setView("search");
    } else if (a.kind === "quest") {
      setSearchTarget({ kind: "quest", ref: a.ref, spoil: 0 });
      setView("search");
    } else if (a.kind === "setting") {
      if (a.id === "motion") themeCtl.setPrefs({ pauseMotion: !themeCtl.prefs.pauseMotion });
      else {
        if (a.id === "doctor" && !doctor.running) doctorNow(); // its output shows in Settings
        setView("settings");
      }
    }
  };

  if (phase === "unpaired") {
    return <Pairing onPaired={() => setBoot((n) => n + 1)} />;
  }

  const chips = stateChips({ phase, reachable, core });
  const last = answers.length ? answers[answers.length - 1] : null;
  const look = voiceLook(voice?.state ?? null, (core?.asking.length ?? 0) > 0);
  const motion = !themeCtl.prefs.pauseMotion;
  const current = viewOf(view);

  const body = (): React.ReactNode => {
    switch (current.id) {
      case "now":
        return <Now core={core} answers={answers} phone={phone} nowS={nowS} act={act} />;
      case "chat":
        return <Chat answers={answers} deep={deep} core={core} onAsk={ask} openCard={setModal} />;
      case "search":
        return <Search target={searchTarget} setTarget={setSearchTarget} />;
      case "costs":
        return <Costs />;
      case "base":
        return <Base />;
      case "settings":
        return (
          <Settings
            core={core}
            session={session}
            slug={themeCtl.slug}
            prefs={themeCtl.prefs}
            setPrefs={themeCtl.setPrefs}
            pin={pin}
            setPin={setPin}
            doctor={doctor}
            runDoctor={doctorNow}
          />
        );
      default:
        return <Later view={current} />;
    }
  };

  return (
    <CardContext.Provider value={actions}>
      <div className={`shell ${mode}${phone ? " phone" : ""}`} data-testid="shell" data-mode={mode} data-socket={socket}>
        <Header chips={chips} core={core} glance={glance} held={held} onLeave={() => setPin("auto")} onSearch={() => setPalette(true)} />
        {!glance && (
          <nav className="gos-shell shell-nav" data-testid="nav" aria-label="Views">
            {(phone ? VIEWS.filter((v) => PHONE_TABS.includes(v.id)) : VIEWS).map((v) => (
              <NavItem
                key={v.id}
                label={v.label}
                keyHint={v.key}
                active={v.id === current.id}
                badge={v.later ? "later" : null}
                onClick={() => setView(v.id)}
                testId={`nav-${v.id}`}
              />
            ))}
          </nav>
        )}
        <main className="shell-view" data-testid="view" data-view={glance ? "glance" : current.id}>
          {notice && (
            <div className="notice gos-panel" role="alert">
              <span>{notice}</span>
              <button type="button" className="gos-btn" onClick={() => setNotice(null)}>
                Dismiss
              </button>
            </div>
          )}
          <Banners banners={bannersFor(core)} decide={decide} turnOn={turnOn} />
          {glance ? (
            <Glance core={core} answers={answers} slug={themeCtl.slug} look={look} motion={motion} nextCard={nextCard} open={openFromGlance} act={act} />
          ) : (
            body()
          )}
        </main>
        {!glance && !phone && <Rail core={core} act={act} />}
        <VoiceStrip slug={themeCtl.slug} look={look} motion={motion} last={last} glance={glance} onAsk={ask} />
        {palette && !glance && <CommandPalette onRun={runPalette} onClose={() => setPalette(false)} />}
        {modal && (
          <div className="modal-backdrop" onMouseDown={() => setModal(null)} data-testid="card-modal">
            <div className="modal gos-panel" role="dialog" aria-label="Card" onMouseDown={(e) => e.stopPropagation()}>
              <div className="modal-head">
                <button type="button" className="gos-btn" onClick={() => setModal(null)} data-testid="modal-close">
                  Close
                </button>
              </div>
              <Card card={modal} />
            </div>
          </div>
        )}
      </div>
    </CardContext.Provider>
  );
}
