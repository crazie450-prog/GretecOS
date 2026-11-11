// Pairing a new browser (D117): ask to pair, then type the 8-digit code shown on monitor 2 (or by
// `gos pair`). The code never comes back to this device; the cookie it gets is HttpOnly.
import { type FormEvent, useState } from "react";
import { ApiError, api } from "../api/client";
import { Button } from "../components/core";
import { GLYPH } from "../state/states";

function defaultName(): string {
  return window.matchMedia?.("(max-width: 760px)").matches ? "Phone" : "Monitor 2";
}

export function Pairing(props: { onPaired: () => void }) {
  const [name, setName] = useState(defaultName);
  const [request, setRequest] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const start = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.pairStart(name.trim() || defaultName());
      setRequest(r.request_id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const complete = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    if (!request) return;
    setBusy(true);
    setError(null);
    try {
      await api.pairComplete(request, code);
      props.onPaired();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
      if (err instanceof ApiError && (err.status === 404 || /closed/.test(err.message))) setRequest(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pairing" data-testid="pairing">
      <section className="gos-panel pairing-panel">
        <div className="wordmark">
          GREGTEC <span className="slash">/</span> OS
        </div>
        <h1 className="gos-title">Pair this device</h1>
        {request === null ? (
          <form onSubmit={(e) => void start(e)} className="pair-form">
            <label className="gos-label" htmlFor="device-name">
              Name this device
            </label>
            <input id="device-name" className="ask-input" value={name} onChange={(e) => setName(e.target.value)} maxLength={40} data-testid="pair-name" />
            <Button type="submit" variant="primary" disabled={busy} testId="pair-start">
              Ask to pair
            </Button>
          </form>
        ) : (
          <form onSubmit={(e) => void complete(e)} className="pair-form">
            <p className="gos-sub">
              {GLYPH.waiting} Type the 8-digit code shown on monitor 2 (a paired dashboard), or run <code>gos pair</code> on the PC.
            </p>
            <input
              className="ask-input code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="1234 5678"
              aria-label="Pairing code"
              data-testid="pair-code"
            />
            <Button type="submit" variant="confirm" disabled={busy || code.replace(/\D/g, "").length !== 8} testId="pair-complete">
              Pair
            </Button>
          </form>
        )}
        {error && (
          <p className="gos-status err" role="alert">
            {GLYPH.warn} {error}
          </p>
        )}
      </section>
    </div>
  );
}
