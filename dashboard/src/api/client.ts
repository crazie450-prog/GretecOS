// The core's REST API, same origin. The paired device's cookie (HttpOnly, SameSite=Strict) goes
// with every request; nothing here ever sees it (D117).
import type { ApprovalCard, Card } from "../cards/types.gen";
import type {
  AiState,
  Answer,
  BaseFind,
  CoreState,
  CreditChoice,
  CreditState,
  DeepTask,
  Device,
  Health,
  MyBase,
  Reply,
  Session,
  Usage,
  Verify,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "the core isn't answering");
  }
  let data: unknown = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok) {
    const why = (data as { error?: unknown } | null)?.error;
    throw new ApiError(response.status, typeof why === "string" ? why : `HTTP ${response.status}`);
  }
  return data as T;
}

const get = <T,>(path: string): Promise<T> => request<T>("GET", path);
const post = <T,>(path: string, body: unknown = {}): Promise<T> => request<T>("POST", path, body);

export const api = {
  session: () => get<Session>("/api/session"),
  state: () => get<CoreState>("/api/state"),
  health: () => get<Health>("/api/health"),
  usage: () => get<Usage>("/api/usage"),
  answers: () => get<Answer[]>("/api/answers"),
  deepTasks: () => get<DeepTask[]>("/api/deep/tasks"),
  ask: (text: string) => post<Reply>("/api/ask", { text }),
  tool: (name: string, args: Record<string, unknown>) => post<{ card: Card }>(`/api/tools/${name}`, { args }),
  recipe: (ref: string) => get<Card>(`/api/recipe?ref=${encodeURIComponent(ref)}`),
  verify: () => get<Verify>("/api/verify"),
  doctor: () => post<{ exit: number; output: string }>("/api/doctor"),
  devices: () => get<Device[]>("/api/devices"),
  revokeDevice: (id: string) => request<unknown>("DELETE", `/api/devices/${encodeURIComponent(id)}`),
  // No-AI mode and the D48 credit flow (WO-2.8): his switch, and his answer to a credit alert.
  aiSwitch: (online: boolean) => post<{ ai: AiState; credits: CreditState | null }>("/api/ai", { online }),
  creditDecision: (choice: CreditChoice) =>
    post<{ choice: CreditChoice; open: string | null; stopping: boolean; ai: AiState; credits: CreditState }>(
      "/api/credits/decision",
      { choice },
    ),
  // My base (WO-3.8): every edit answers with the whole of it.
  base: () => get<MyBase>("/api/base"),
  baseFind: (q: string, kind: "item" | "machine") =>
    get<BaseFind>(`/api/base/find?q=${encodeURIComponent(q)}&kind=${kind}`),
  baseTier: (stage: string | null) => post<MyBase>("/api/base/tier", { stage }),
  baseMachine: (mteId: number, count: number) => post<MyBase>("/api/base/machines", { mte_id: mteId, count }),
  baseMachineClear: (mteId: number) => request<MyBase>("DELETE", `/api/base/machines/${mteId}`),
  baseStock: (key: string, qty: number) => post<MyBase>("/api/base/stock", { key, qty }),
  baseStockRemove: (key: string) => request<MyBase>("DELETE", `/api/base/stock?key=${encodeURIComponent(key)}`),
  pairStart: (name: string) => post<{ request_id: string; expires_in: number }>("/api/pair/start", { name }),
  pairComplete: (requestId: string, code: string) =>
    post<{ device_id: string; name: string }>("/api/pair/complete", { request_id: requestId, code }),
};

/** Run an approval card's action against the endpoint its kind belongs to. */
export async function act(card: ApprovalCard, action: string): Promise<void> {
  const [kind, ...rest] = card.id.split("-");
  const id = encodeURIComponent(rest.join("-"));
  if (card.kind === "deep_budget" && kind === "deep" && (action === "approve" || action === "stop")) {
    await post(`/api/deep/tasks/${id}/${action}`);
    return;
  }
  if (card.kind === "pairing" && kind === "pair" && action === "deny") {
    await post(`/api/pair/${id}/deny`);
    return;
  }
  throw new ApiError(400, `${action} isn't an action for this card`);
}
