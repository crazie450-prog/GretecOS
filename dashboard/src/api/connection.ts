// Getting to the core: the session check (paired or not), the first snapshot, then the WebSocket
// for pushes, reconnecting with back-off. A core that stops answering is the "core offline" state.
import { addTasks } from "../state/deep";
import { addAnswers, applyMessage, type Store } from "../state/store";
import { ApiError, api } from "./client";

const BACKOFF_MS = [1000, 2000, 4000, 8000, 15000];
const PING_MS = 20000;
const RETRY_MS = 3000;

export function socketUrl(location: { protocol: string; host: string }): string {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
}

/** The dashboard build a core's hello names (D159: the SHA-256 of the index.html it serves). */
export function helloBuild(message: unknown): string | null {
  if (typeof message !== "object" || message === null) return null;
  const m = message as { type?: unknown; build?: unknown };
  return m.type === "hello" && typeof m.build === "string" ? m.build : null;
}

/** Whether a page that loaded build `loaded` runs old code: the core now serves another build
 * (a deploy restarted it), so the page reloads instead of drawing with an older card schema. */
export function staleBuild(loaded: string | null, served: string | null): boolean {
  return loaded !== null && served !== null && served !== loaded;
}

export interface Connection {
  stop: () => void;
}

export function connect(store: Store): Connection {
  let stopped = false;
  let socket: WebSocket | null = null;
  let attempt = 0;
  let ping: ReturnType<typeof setInterval> | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let build: string | null = null; // the build this page loaded: the first hello names it

  const later = (fn: () => void, ms: number): void => {
    if (!stopped) timer = setTimeout(fn, ms);
  };

  async function boot(): Promise<void> {
    try {
      const session = await api.session();
      store.set({ session, phase: "ready", reachable: true, error: null });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        store.set({ phase: "unpaired", reachable: true, session: null });
        return; // the pairing screen calls connect() again once paired
      }
      store.set({ reachable: false, error: e instanceof Error ? e.message : String(e) });
      later(() => void boot(), RETRY_MS);
      return;
    }
    await refresh();
    open();
  }

  async function refresh(): Promise<void> {
    // A core without the deep lane answers 503 for its tasks: Chat then shows answers only.
    const [state, answers, deep] = await Promise.allSettled([api.state(), api.answers(), api.deepTasks()]);
    store.set((s) => ({
      core: state.status === "fulfilled" ? state.value : s.core,
      answers: answers.status === "fulfilled" ? addAnswers(s.answers, answers.value) : s.answers,
      deep: deep.status === "fulfilled" ? addTasks(s.deep, deep.value) : s.deep,
    }));
  }

  function open(): void {
    if (stopped) return;
    store.set({ socket: "connecting" });
    const ws = new WebSocket(socketUrl(window.location));
    socket = ws;
    ws.onopen = () => {
      attempt = 0;
      store.set({ socket: "open", reachable: true });
      ping = setInterval(() => ws.readyState === WebSocket.OPEN && ws.send('{"type":"ping"}'), PING_MS);
    };
    ws.onmessage = (event) => {
      let message: unknown;
      try {
        message = JSON.parse(String(event.data));
      } catch {
        return;
      }
      const served = helloBuild(message);
      if (staleBuild(build, served)) {
        window.location.reload(); // after a deploy: the view is in the URL, the look in storage
        return;
      }
      build = build ?? served;
      store.set((s) => applyMessage(s, message));
    };
    ws.onclose = (event) => {
      clearInterval(ping);
      store.set({ socket: "closed" });
      if (stopped) return;
      if (event.code === 1008) {
        // Not paired, or this device was revoked: back to the session check.
        later(() => void boot(), 500);
        return;
      }
      store.set({ reachable: false });
      const wait = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
      attempt += 1;
      later(() => void reopen(), wait);
    };
  }

  async function reopen(): Promise<void> {
    try {
      await api.session();
      store.set({ reachable: true });
      await refresh();
      open();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        store.set({ phase: "unpaired", session: null });
        return;
      }
      const wait = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
      attempt += 1;
      later(() => void reopen(), wait);
    }
  }

  void boot();
  return {
    stop() {
      stopped = true;
      clearTimeout(timer);
      clearInterval(ping);
      socket?.close();
    },
  };
}
