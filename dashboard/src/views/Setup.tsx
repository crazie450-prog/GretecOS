// The first-run setup wizard (WO-2.9, plan 7.14 rule 8; the approved "First-run setup wizard"
// board, D92, built to the design system without the canvas handoff, D158 (5)). It draws every
// step the core's registry lists from the step's own description, so a later phase's step needs
// no code here. Settings go to gos.toml; secrets go to the keyring and never come back.
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { SetupCheck, SetupDevices, SetupField, SetupState, SetupStep } from "../api/types";
import { Button, StatusGlyph } from "../components/core";
import { canFinish, deviceChoices, startValues, stepMark } from "../state/setup";
import { GLYPH } from "../state/states";

const MARK_GLYPH = { done: GLYPH.done, current: GLYPH.progress, todo: GLYPH.todo } as const;

function message(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e);
}

/** The problems and checklist rows a refused save or finish came back with. */
function refusal(e: unknown): { problems: Record<string, string>; checks: SetupCheck[] | null } {
  const body = e instanceof ApiError ? (e.body as { problems?: Record<string, string>; checks?: SetupCheck[] } | null) : null;
  return { problems: body?.problems ?? {}, checks: body?.checks ?? null };
}

function Checks(props: { checks: SetupCheck[] | null; testId: string }) {
  if (props.checks === null) return null;
  return (
    <ul className="setup-checks" data-testid={props.testId}>
      {props.checks.map((c) => (
        <li key={c.id} data-check={c.id} data-status={c.status}>
          <StatusGlyph status={c.status} label={c.label} />
          <span className="setup-detail">{c.detail}</span>
        </li>
      ))}
    </ul>
  );
}

/** A secret: typed once, stored, never shown again. Remove takes it out; Test makes the step's
 * test call with the stored one. */
function SecretField(props: { field: SetupField; onChange: () => void }) {
  const f = props.field;
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const name = f.secret ?? f.id;

  const store = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    setBusy(true);
    setNote(null);
    try {
      const r = await api.setupSecret(name, value);
      setNote({ ok: true, text: r.replaced ? "Replaced." : "Stored." });
      props.onChange();
    } catch (err) {
      setNote({ ok: false, text: message(err) });
    } finally {
      setValue(""); // never kept in the page, stored or not
      setBusy(false);
    }
  };
  const remove = async (): Promise<void> => {
    setBusy(true);
    try {
      await api.setupSecretDelete(name);
      setNote({ ok: true, text: "Removed." });
      props.onChange();
    } catch (err) {
      setNote({ ok: false, text: message(err) });
    } finally {
      setBusy(false);
    }
  };
  const test = async (): Promise<void> => {
    setBusy(true);
    setNote({ ok: true, text: `${GLYPH.progress} Testing` });
    try {
      const r = await api.setupSecretTest(name);
      setNote({ ok: r.ok, text: r.detail });
    } catch (err) {
      setNote({ ok: false, text: message(err) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="setup-field" onSubmit={(e) => void store(e)} data-field={f.id}>
      <label className="gos-label" htmlFor={`setup-${f.id}`}>
        {f.label}
      </label>
      <div className="setup-row">
        <input
          id={`setup-${f.id}`}
          className="ask-input"
          type="password"
          autoComplete="off"
          spellCheck={false}
          placeholder={f.stored ? "Stored · type a new one to replace it" : f.placeholder}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          data-testid={`setup-secret-${f.id}`}
        />
        <Button type="submit" variant="primary" disabled={busy || !value.trim()} testId={`setup-store-${f.id}`}>
          Store
        </Button>
      </div>
      <div className="setup-row">
        <span data-testid={`setup-stored-${f.id}`} data-stored={f.stored ? "yes" : "no"}>
          {f.stored ? <StatusGlyph status="ok" label="Stored in the keyring" /> : <StatusGlyph status="info" glyph={GLYPH.todo} label="Not stored" />}
        </span>
        {f.stored && f.test && (
          <Button onClick={() => void test()} disabled={busy} testId={`setup-test-${f.id}`}>
            Test
          </Button>
        )}
        {f.stored && (
          <Button onClick={() => void remove()} disabled={busy} testId={`setup-remove-${f.id}`}>
            Remove
          </Button>
        )}
      </div>
      {f.help && <p className="setup-help">{f.help}</p>}
      {note && (
        <p className={`setup-note ${note.ok ? "" : "bad"}`} role="status" data-testid={`setup-note-${f.id}`}>
          {note.ok ? "" : `${GLYPH.warn} `}
          {note.text}
        </p>
      )}
    </form>
  );
}

function ValueField(props: {
  field: SetupField;
  value: string;
  problem: string | undefined;
  devices: SetupDevices | null;
  onChange: (v: string) => void;
}) {
  const f = props.field;
  const id = `setup-${f.id}`;
  const listId = f.suggestions.length ? `${id}-list` : undefined;
  let input: React.ReactNode;
  if (f.kind === "choice" || f.kind === "device") {
    const choices = f.kind === "choice" ? f.options : deviceChoices(f, props.devices, props.value);
    input = (
      <select id={id} className="ask-input" value={props.value} onChange={(e) => props.onChange(e.target.value)} data-testid={`setup-field-${f.id}`}>
        {choices.map((c) => (
          <option key={c.value} value={c.value}>
            {c.label}
          </option>
        ))}
      </select>
    );
  } else {
    input = (
      <input
        id={id}
        className="ask-input"
        value={props.value}
        placeholder={f.placeholder}
        spellCheck={false}
        list={listId}
        onChange={(e) => props.onChange(e.target.value)}
        aria-invalid={props.problem ? true : undefined}
        data-testid={`setup-field-${f.id}`}
      />
    );
  }
  return (
    <div className="setup-field" data-field={f.id}>
      <label className="gos-label" htmlFor={id}>
        {f.label}
        {f.required ? "" : " · optional"}
      </label>
      {input}
      {listId && (
        <datalist id={listId}>
          {f.suggestions.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
      )}
      {(f.help || f.key) && (
        <p className="setup-help">
          {f.help}
          {f.key ? <span className="setup-key"> {f.key}</span> : null}
        </p>
      )}
      {props.problem && (
        <p className="setup-note bad" role="alert" data-testid={`setup-problem-${f.id}`}>
          {GLYPH.warn} {props.problem}
        </p>
      )}
    </div>
  );
}

function StepView(props: {
  step: SetupStep;
  first: boolean;
  last: boolean;
  done: boolean;
  onBack: () => void;
  onNext: () => void;
  onSaved: () => void;
  onSecret: () => void;
  onFinished: () => void;
}) {
  const step = props.step;
  const valueFields = step.fields.filter((f) => f.kind !== "secret" && f.kind !== "checks");
  const secretFields = step.fields.filter((f) => f.kind === "secret");
  const checklist = step.fields.some((f) => f.kind === "checks");
  const [values, setValues] = useState(() => startValues(step));
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [checks, setChecks] = useState<SetupCheck[] | null>(null);
  const [devices, setDevices] = useState<SetupDevices | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const rerun = useCallback(async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      setChecks((await api.setupChecklist()).checks);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (step.fields.some((f) => f.kind === "device")) api.setupDevices().then(setDevices, (e: unknown) => setError(message(e)));
    if (checklist) void rerun();
  }, [step, checklist, rerun]);

  const check = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.setupCheck(step.id, values);
      setProblems(r.problems);
      setChecks(r.checks);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  const save = async (e?: FormEvent): Promise<void> => {
    e?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.setupSave(step.id, values);
      setProblems({});
      setChecks(r.checks);
      props.onSaved();
    } catch (err) {
      const found = refusal(err);
      setProblems(found.problems);
      if (found.checks) setChecks(found.checks);
      setError(message(err));
    } finally {
      setBusy(false);
    }
  };

  const finish = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.setupFinish();
      setChecks(r.checks);
      props.onFinished();
    } catch (err) {
      const found = refusal(err);
      if (found.checks) setChecks(found.checks);
      setError(message(err));
    } finally {
      setBusy(false);
    }
  };

  const secretsDone = secretFields.every((f) => f.stored);
  return (
    <section className="setup-step" data-testid={`setup-step-${step.id}`} aria-labelledby="setup-step-title">
      <h2 className="gos-title" id="setup-step-title">
        {step.title}
        {step.optional ? <span className="setup-optional"> · optional</span> : null}
      </h2>
      <p className="gos-sub">{step.intro}</p>
      {secretFields.map((f) => (
        <SecretField key={f.id} field={f} onChange={props.onSecret} />
      ))}
      {valueFields.length > 0 && (
        <form className="setup-form" onSubmit={(e) => void save(e)}>
          {valueFields.map((f) => (
            <ValueField
              key={f.id}
              field={f}
              value={values[f.id] ?? ""}
              problem={problems[f.id]}
              devices={devices}
              onChange={(v) => setValues((old) => ({ ...old, [f.id]: v }))}
            />
          ))}
        </form>
      )}
      {devices?.provider === "fake" && <p className="setup-help">{GLYPH.waiting} Test devices: this core isn't on Windows.</p>}
      {checklist && (
        <p className="setup-help">
          {GLYPH.waiting} Saved settings take effect when GOS next starts (log in again, or run gos stop and then gos serve). Secrets work at once.
        </p>
      )}
      <Checks checks={checks} testId="setup-checks" />
      {error && (
        <p className="setup-note bad" role="alert" data-testid="setup-error">
          {GLYPH.warn} {error}
        </p>
      )}
      <div className="setup-actions">
        {!props.first && (
          <Button onClick={props.onBack} disabled={busy} testId="setup-back">
            Back
          </Button>
        )}
        {valueFields.length > 0 && (
          <>
            <Button onClick={() => void check()} disabled={busy} testId="setup-check">
              Check
            </Button>
            <Button variant="primary" onClick={() => void save()} disabled={busy} testId="setup-save">
              {props.last ? "Save" : "Save and continue"}
            </Button>
          </>
        )}
        {valueFields.length === 0 && !checklist && (
          <Button variant={secretsDone ? "primary" : "plain"} onClick={props.onNext} disabled={busy} testId="setup-next">
            {secretsDone || !step.optional ? "Continue" : "Skip for now"}
          </Button>
        )}
        {checklist && (
          <>
            <Button onClick={() => void rerun()} disabled={busy} testId="setup-rerun">
              {busy ? `${GLYPH.progress} Checking` : "Re-run checks"}
            </Button>
            <Button variant="primary" onClick={() => void finish()} disabled={busy || !canFinish(checks)} testId="setup-finish">
              {props.done ? "Done" : "Finish setup"}
            </Button>
          </>
        )}
      </div>
    </section>
  );
}

/** Above every view when gos.toml exists but setup was never finished (an install from before the
 * wizard, D167): a way into the wizard that never blocks the dashboard. Designed to the system
 * without a board: needs Clayton's board approval (D92, D158). */
export function SetupBanner(props: { open: () => void; dismiss: () => void }) {
  return (
    <section className="gos-panel banner banner-ai" data-testid="banner-setup" role="status">
      <span className="gos-label banner-head">
        <span className="banner-glyph" aria-hidden="true">
          {GLYPH.warn}
        </span>{" "}
        Setup isn't finished
      </span>
      <p className="gos-sub">gos.toml is there, but the setup wizard's checklist hasn't been run. Everything works meanwhile.</p>
      <div className="actions">
        <Button variant="primary" onClick={props.open} testId="setup-banner-open">
          Open setup
        </Button>
        <Button onClick={props.dismiss} testId="setup-banner-dismiss">
          Dismiss
        </Button>
      </div>
    </section>
  );
}

export function Setup(props: { onClose: () => void; onDone: () => void }) {
  const [state, setState] = useState<SetupState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [index, setIndex] = useState(0);
  const [saved, setSaved] = useState<ReadonlySet<string>>(new Set());

  const load = useCallback(async (): Promise<void> => {
    try {
      setState(await api.setup());
      setError(null);
    } catch (e) {
      setError(message(e));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  if (state === null) {
    return (
      <div className="setup" data-testid="setup">
        <section className="gos-panel setup-panel">
          <p className="empty">{error ? `${GLYPH.warn} ${error}` : `${GLYPH.progress} Loading setup`}</p>
        </section>
      </div>
    );
  }
  const steps = state.steps;
  const step = steps[Math.min(index, steps.length - 1)];
  const go = (i: number): void => setIndex(Math.max(0, Math.min(steps.length - 1, i)));
  return (
    <div className="setup" data-testid="setup" data-done={state.done ? "yes" : "no"}>
      <section className="gos-panel setup-panel">
        <header className="setup-head">
          <div className="wordmark">
            GREGTEC <span className="slash">/</span> OS
          </div>
          <div className="setup-title">
            <h1 className="page-title">{state.done ? "Setup" : "First-run setup"}</h1>
            <p className="gos-sub">
              Settings go into <code>{state.config_path}</code>; keys and tokens into Windows Credential Manager, never into a file.
            </p>
          </div>
          {/* Only a first run (no gos.toml) has no way out: GOS has nothing to run with yet. */}
          {(state.done || state.config_exists) && (
            <Button onClick={props.onClose} testId="setup-close">
              {state.done ? "Close" : "Later"}
            </Button>
          )}
        </header>
        {state.config_error && (
          <p className="setup-note bad" role="alert">
            {GLYPH.warn} {state.config_error}
          </p>
        )}
        <div className="setup-body">
          <nav aria-label="Setup steps">
            <ol className="setup-steps">
              {steps.map((s, i) => {
                const mark = stepMark(s, i, index, saved, state.done);
                return (
                  <li key={s.id}>
                    <button
                      type="button"
                      className={`setup-step-link ${mark}`}
                      aria-current={mark === "current" ? "step" : undefined}
                      onClick={() => go(i)}
                      data-testid={`setup-nav-${s.id}`}
                      data-mark={mark}
                    >
                      <span aria-hidden="true" className="setup-glyph">
                        {MARK_GLYPH[mark]}
                      </span>
                      <span className="setup-num">{i + 1}</span>
                      <span>{s.title}</span>
                    </button>
                  </li>
                );
              })}
            </ol>
          </nav>
          <StepView
            key={step.id}
            step={step}
            first={index === 0}
            last={index === steps.length - 1}
            done={state.done}
            onBack={() => go(index - 1)}
            onNext={() => go(index + 1)}
            onSaved={() => {
              setSaved((old) => new Set([...old, step.id]));
              void load();
              go(index + 1);
            }}
            onSecret={() => void load()}
            onFinished={() => {
              void load();
              props.onDone();
            }}
          />
        </div>
      </section>
    </div>
  );
}
