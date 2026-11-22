// A design job in a view (WO-3.9, D353, D354): start it, follow its stage (the core's `job`
// pushes, and a poll of GET /api/design-jobs/{id} each second so a dropped socket never strands it),
// show the stage, the time it has run and a Cancel, and hand the result to the view when it is
// done. Leaving the view stops following, not the job: the core finishes and saves it, and the
// view follows it again when it opens while the job runs (the state's running jobs). Built from
// the design system's busy panel, StatusGlyph glyph and Button; needs Clayton's board approval
// (D92).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type { DesignJob, JobStart } from "../api/types";
import type { ChoicesCard } from "../cards/types.gen";
import { elapsed, ended, endedText, newer, runningOf, stageText } from "../state/jobs";
import { GLYPH } from "../state/states";
import { useApp } from "../state/store";
import { Button, useNow } from "./core";

export const POLL_MS = 1000;

export interface JobRunner {
  job: DesignJob | null;
  /** Start a job; resolves to the clarifying question when the core asks one (no job). */
  start: (call: () => Promise<JobStart>, then?: (result: unknown) => void) => Promise<ChoicesCard | null>;
  cancel: () => void;
}

export function useDesignJob(
  kind: DesignJob["kind"],
  onDone: (result: unknown) => void,
  onEnded: (text: string) => void,
): JobRunner {
  const [job, setJob] = useState<DesignJob | null>(null);
  const done = useRef(onDone);
  const fail = useRef(onEnded);
  const then = useRef<((result: unknown) => void) | null>(null);
  const poke = useRef<() => void>(() => {});
  done.current = onDone;
  fail.current = onEnded;
  const jobId = job?.id ?? null;
  const pushed = useApp((s) => (jobId ? s.jobs[jobId] : undefined));

  // A view opened while its kind's job runs follows that job, also when the core's state arrives
  // after the view mounted (a full page load: the state is null until the socket's first push).
  const runningId = useApp((s) => runningOf(s.core, kind));
  useEffect(() => {
    const id = runningId;
    if (!id) return;
    let live = true;
    api.job(id).then(
      (r) => live && !ended(r.job) && setJob((prev) => prev ?? r.job),
      () => {},
    );
    return () => {
      live = false;
    };
  }, [runningId]);

  useEffect(() => {
    if (!jobId) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const finish = (j: DesignJob): void => {
      live = false;
      clearTimeout(timer);
      setJob(null);
      const cb = then.current ?? done.current;
      then.current = null;
      if (j.status === "done") cb(j.result);
      else fail.current(endedText(j));
    };
    const tick = async (): Promise<void> => {
      try {
        const { job: j } = await api.job(jobId);
        if (!live) return;
        if (ended(j)) {
          finish(j);
          return;
        }
        setJob((prev) => newer(prev, j));
      } catch (e) {
        if (!live) return;
        if (e instanceof ApiError && e.status === 404) {
          live = false;
          setJob(null);
          fail.current("The core restarted and the design was lost: start it again.");
          return;
        }
      }
      if (live) timer = setTimeout(() => void tick(), POLL_MS);
    };
    poke.current = () => {
      if (!live) return;
      clearTimeout(timer);
      void tick();
    };
    timer = setTimeout(() => void tick(), 0);
    return () => {
      live = false;
      clearTimeout(timer);
      poke.current = () => {};
    };
  }, [jobId]);

  // A push shows the new stage at once; an ended push fetches the result now.
  useEffect(() => {
    if (!pushed) return;
    if (ended(pushed)) poke.current();
    else setJob((prev) => newer(prev, pushed));
  }, [pushed]);

  const start = useCallback(
    async (call: () => Promise<JobStart>, after?: (result: unknown) => void): Promise<ChoicesCard | null> => {
      const out = await call();
      if ("clarify" in out) return out.clarify;
      then.current = after ?? null;
      setJob(out.job);
      return null;
    },
    [],
  );

  const cancel = useCallback(() => {
    if (!jobId) return;
    api.cancelJob(jobId).then(
      (r) => setJob((prev) => (prev && prev.id === r.job.id ? { ...prev, cancel_asked: r.job.cancel_asked } : prev)),
      () => {},
    );
  }, [jobId]);

  return { job, start, cancel };
}

/** A running job's time, from the core's `elapsed_s` and when this device received it. */
export function useElapsed(id: string, elapsedS: number): string {
  const now = useNow(1000, true);
  // A new reading (another job, or a new elapsed_s from a push or a poll) resets the base.
  const at = useMemo(() => Date.now(), [id, elapsedS]);
  return elapsed(elapsedS, at, Math.max(now, at));
}

/** The busy panel with the job's stage, its time and Cancel (the view's own busy state). */
export function JobProgress(props: { job: DesignJob; onCancel: () => void; testId: string; prefix: string }) {
  const j = props.job;
  const time = useElapsed(j.id, j.elapsed_s);
  const saving = j.stage === "saving";
  const box = useRef<HTMLDivElement | null>(null);
  // On the phone the panel is below the form: bring it into view when the job starts.
  useEffect(() => {
    box.current?.scrollIntoView?.({ block: "nearest" });
  }, []);
  return (
    <div className="gos-panel" aria-busy="true" data-testid={props.testId} data-job={j.id} ref={box}>
      <div className="job-progress">
        <span className="empty" data-testid={`${props.prefix}-job-stage`}>
          {GLYPH.progress} {stageText(j)}
        </span>
        <span className="gos-src num" data-testid={`${props.prefix}-job-elapsed`} title="Time since the design started">
          {time}
        </span>
        <Button
          onClick={props.onCancel}
          disabled={j.cancel_asked || saving}
          title={saving ? "Saving: too late to cancel" : "Stop the design; nothing is saved"}
          testId={`${props.prefix}-cancel`}
        >
          {j.cancel_asked ? `${GLYPH.paused} Cancelling` : "Cancel"}
        </Button>
      </div>
    </div>
  );
}
