// Glance or workbench (plan 7.14, D49, D161). Glance while GTNH has focus: large type, still
// clickable. Workbench when the mouse is on monitor 2, the game is paused or closed, or the
// dashboard window itself has focus. Moving the mouse into the window switches at once; leaving
// it switches back after a short hold, so crossing the screen edge doesn't flicker.
export type Mode = "glance" | "workbench";
export type ModePin = "auto" | Mode;

export const LEAVE_HOLD_MS = 1500;
export const GLANCE_FPS = 30; // glance mode's frame cap (plan 7.14 rule 6)

export interface ModeInputs {
  phone: boolean; // the phone layout has no glance mode (D53)
  pin: ModePin; // Settings: automatic, or held in one mode
  pointerInside: boolean;
  msSinceLeave: number; // since the pointer last left the window (Infinity: never inside)
  windowFocused: boolean;
  gameRunning: string[] | null; // null: unknown
  gameFocused: boolean | null; // the core's focus signal; null: unknown
  gamePaused: boolean | null;
}

export function modeFor(i: ModeInputs): Mode {
  if (i.phone) return "workbench";
  if (i.pin !== "auto") return i.pin;
  if (i.gameRunning !== null && i.gameRunning.length === 0) return "workbench"; // game closed
  if (i.gamePaused === true) return "workbench";
  if (i.pointerInside || i.msSinceLeave < LEAVE_HOLD_MS) return "workbench";
  if (i.windowFocused && i.gameFocused !== true) return "workbench";
  return "glance";
}

/** Frame throttle for glance's animations: draw only when a frame's time is due. */
export function frameDue(lastMs: number, nowMs: number, fps: number): boolean {
  return nowMs - lastMs >= 1000 / fps - 0.5;
}
