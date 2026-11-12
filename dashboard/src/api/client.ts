// The core's REST API, same origin. The paired device's cookie (HttpOnly, SameSite=Strict) goes
// with every request; nothing here ever sees it (D117).
import type { ApprovalCard, Card } from "../cards/types.gen";
import type {
  Accepted,
  AiState,
  Alternatives,
  DesignAnswer,
  Designed,
  DesignerView,
  DesignSpec,
  SavedPlanRow,
  Answer,
  BaseFind,
  CoreState,
  CreditChoice,
  CreditState,
  DeepTask,
  Device,
  Health,
  MyBase,
  OresView,
  CropsView,
  PersonaInfo,
  PersonaState,
  QuestsView,
  Reply,
  Session,
  SetupCheck,
  SetupDevices,
  SetupState,
  Usage,
  Verify,
  WaypointProposal,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly body: unknown = null, // the refusal's JSON (the wizard's problems and checks)
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
    throw new ApiError(response.status, typeof why === "string" ? why : `HTTP ${response.status}`, data);
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
  // The Quests view (WO-6.4): a spoil level only when he asks for more ("Spoil it", a tap).
  quests: (spoil: number | null = null) => get<QuestsView>(spoil === null ? "/api/quests" : `/api/quests?spoil=${spoil}`),
  // The Ores view (WO-6.6): a material's veins, and a vein's waypoint proposed by its vein: id.
  ores: (material: string | null = null, dim: number | null = null) => {
    const q = new URLSearchParams();
    if (material) q.set("material", material);
    if (dim !== null) q.set("dim", String(dim));
    const s = q.toString();
    return get<OresView>(s ? `/api/ores?${s}` : "/api/ores");
  },
  oreWaypoint: (vein: string) => post<WaypointProposal>("/api/ores/waypoint", { vein }),
  // The Crops view (WO-6.7): his crops, the target and its guide; edits answer with the view.
  crops: (crop: string | null = null) =>
    get<CropsView>(crop ? `/api/crops?crop=${encodeURIComponent(crop)}` : "/api/crops"),
  cropHave: (crop: string, stats: number[]) => post<CropsView>("/api/crops/have", { crop, stats }),
  cropHaveRemove: (crop: string) => request<CropsView>("DELETE", `/api/crops/have?crop=${encodeURIComponent(crop)}`),
  cropTarget: (crop: string | null) => post<CropsView>("/api/crops/target", { crop }),
  // My base (WO-3.8): every edit answers with the whole of it.
  base: () => get<MyBase>("/api/base"),
  baseFind: (q: string, kind: "item" | "machine") =>
    get<BaseFind>(`/api/base/find?q=${encodeURIComponent(q)}&kind=${kind}`),
  baseTier: (stage: string | null) => post<MyBase>("/api/base/tier", { stage }),
  baseMachine: (mteId: number, count: number) => post<MyBase>("/api/base/machines", { mte_id: mteId, count }),
  baseMachineClear: (mteId: number) => request<MyBase>("DELETE", `/api/base/machines/${mteId}`),
  baseStock: (key: string, qty: number) => post<MyBase>("/api/base/stock", { key, qty }),
  baseStockRemove: (key: string) => request<MyBase>("DELETE", `/api/base/stock?key=${encodeURIComponent(key)}`),
  // The Designer (WO-3.7, D250): routes ranked by the core's code; every change re-solves there.
  designerLatest: () => get<{ ids: string[] }>("/api/designer"),
  plans: () => get<{ plans: SavedPlanRow[]; problems: string[] }>("/api/plans"),
  designerView: (id: string) => get<DesignerView>(`/api/plans/${encodeURIComponent(id)}/designer`),
  design: (spec: DesignSpec) => post<DesignAnswer>("/api/designer/design", spec),
  alternatives: (id: string, node: string, all: boolean) =>
    get<Alternatives>(
      `/api/plans/${encodeURIComponent(id)}/alternatives?node=${encodeURIComponent(node)}${all ? "&all=1" : ""}`,
    ),
  pin: (id: string, node: string, recipe: string | null) =>
    post<Designed>(`/api/plans/${encodeURIComponent(id)}/pin`, { node, recipe }),
  resolve: (id: string) => post<Designed>(`/api/plans/${encodeURIComponent(id)}/resolve`),
  accept: (id: string) => post<Accepted>(`/api/plans/${encodeURIComponent(id)}/accept`),
  rename: (id: string, name: string) =>
    post<{ view: DesignerView }>(`/api/plans/${encodeURIComponent(id)}/name`, { name }),
  // The persona (WO-5.6): the same switch the Discord /persona command will call.
  persona: (id: string) => post<PersonaState & { persona: PersonaInfo }>("/api/persona", { id }),
  // The setup wizard (WO-2.9): admin and paired devices only. A secret goes in and never comes back.
  setup: () => get<SetupState>("/api/setup"),
  setupDevices: () => get<SetupDevices>("/api/setup/devices"),
  setupCheck: (step: string, values: Record<string, string>) =>
    post<{ problems: Record<string, string>; checks: SetupCheck[]; blocked: boolean }>(
      `/api/setup/steps/${encodeURIComponent(step)}/check`,
      { values },
    ),
  setupSave: (step: string, values: Record<string, string>) =>
    post<{ saved: string[]; checks: SetupCheck[] }>(`/api/setup/steps/${encodeURIComponent(step)}`, { values }),
  setupSecret: (name: string, value: string) =>
    post<{ name: string; stored: boolean; replaced: boolean }>(`/api/setup/secrets/${encodeURIComponent(name)}`, { value }),
  setupSecretDelete: (name: string) =>
    request<{ name: string; stored: boolean }>("DELETE", `/api/setup/secrets/${encodeURIComponent(name)}`),
  setupSecretTest: (name: string) =>
    post<{ name: string; ok: boolean; detail: string }>(`/api/setup/secrets/${encodeURIComponent(name)}/test`),
  setupChecklist: () => post<{ checks: SetupCheck[] }>("/api/setup/checklist"),
  setupFinish: () => post<{ done: boolean; completed_utc: string; checks: SetupCheck[] }>("/api/setup/finish"),
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
  // A world action (WO-7.1, D59): the button confirms or cancels this action by its id, so an
  // old card can never confirm a newer one; the core sets confirmed_via from who asked.
  if (card.kind === "action" && kind === "action" && (action === "confirm" || action === "cancel")) {
    await post(`/api/actions/${id}/${action}`);
    return;
  }
  throw new ApiError(400, `${action} isn't an action for this card`);
}
