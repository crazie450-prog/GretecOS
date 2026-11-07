// The core's REST API, same origin. The paired device's cookie (HttpOnly, SameSite=Strict) goes
// with every request; nothing here ever sees it (D117).
import type { ApprovalCard, Card } from "../cards/types.gen";
import type { Answer, CoreState, Device, Health, Reply, Session, Usage, Verify } from "./types";

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
  ask: (text: string) => post<Reply>("/api/ask", { text }),
  tool: (name: string, args: Record<string, unknown>) => post<{ card: Card }>(`/api/tools/${name}`, { args }),
  recipe: (ref: string) => get<Card>(`/api/recipe?ref=${encodeURIComponent(ref)}`),
  verify: () => get<Verify>("/api/verify"),
  doctor: () => post<{ exit: number; output: string }>("/api/doctor"),
  devices: () => get<Device[]>("/api/devices"),
  revokeDevice: (id: string) => request<unknown>("DELETE", `/api/devices/${encodeURIComponent(id)}`),
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
