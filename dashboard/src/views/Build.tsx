// Build (plan 7.10, 7.14): "How do I build this multi?" WO-10.1's spike: the core serves Clayton's
// GuideNH export (GOS_HOME/data/guide-site, /guide/...) and this view frames one of its scene pages.
// The frame's CSP limits which files it loads to /guide/, but the frame is same-origin, so that is
// not isolation: the folder's contents are trusted until WO-10.8 serves /guide/ from its own
// origin (gregtec.build.guide, D304).
// WO-10.8 builds the real viewer UI. Built to the design system: needs Clayton's board approval.
// WO-10.7 adds the decorative shells: the core designs and checks them (gregtec.build.shell); this
// view lists them and draws the chosen one's card (preview, palette, Lab-only paste steps).
import { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { GuideSite, ShellSummary } from "../api/types";
import { Card } from "../cards/Card";
import type { Card as CardData } from "../cards/types.gen";
import { Panel } from "../components/core";
import { GLYPH } from "../state/states";

const mb = (bytes: number): string => `${(bytes / 1_000_000).toFixed(1)} MB`;

/** The frame's address for one page of the export (each path segment encoded). */
export function guideSrc(path: string): string {
  return `/guide/${path.split("/").map(encodeURIComponent).join("/")}`;
}

/** The page the view opens first: one of GOS's own guide (`gos/...`, the Lab steps' spike page) if
 * the export has one, else the first scene page. A full export holds every guide in the pack. */
export function firstPage(pages: GuideSite["pages"]): string | null {
  return (pages.find((p) => p.path.startsWith("gos/")) ?? pages[0])?.path ?? null;
}

/** The saved shells, newest first; the chosen one's card (WO-10.7, D316). */
export function Shells() {
  const [list, setList] = useState<ShellSummary[] | null>(null);
  const [pick, setPick] = useState<string | null>(null);
  const [card, setCard] = useState<CardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api.shells().then(
      (r) => {
        if (!live) return;
        setList(r.shells);
        setPick(r.shells[0]?.id ?? null);
      },
      (e: unknown) => live && setError(e instanceof ApiError ? e.message : String(e)),
    );
    return () => {
      live = false;
    };
  }, []);
  useEffect(() => {
    if (!pick) return;
    let live = true;
    setCard(null);
    api.shell(pick).then(
      (r) => live && setCard(r.card),
      (e: unknown) => live && setError(e instanceof ApiError ? e.message : String(e)),
    );
    return () => {
      live = false;
    };
  }, [pick]);
  return (
    <Panel
      label="Shells"
      testId="shells"
      aside={list ? <span className="gos-src">{list.length} saved</span> : undefined}
    >
      {error && <p className="empty">{GLYPH.warn} {error}</p>}
      {!list && !error && <p className="empty">{GLYPH.progress} Looking for shells</p>}
      {list && !list.length && (
        <p className="gos-sub" data-testid="shells-empty">
          {GLYPH.todo} No shell yet. Ask for one ("design a foundry shell around my steel line"), or run gos shell design
          with the plan's id.
        </p>
      )}
      {list && list.length > 1 && (
        <select
          className="ask-input base-select"
          aria-label="Shell"
          value={pick ?? ""}
          onChange={(e) => setPick(e.target.value)}
          data-testid="shell-pick"
        >
          {list.map((s) => (
            <option key={s.id} value={s.id}>
              {s.id} · {s.theme_name ?? "no theme"} · {s.source} · plans {s.plans.join(", ")}
            </option>
          ))}
        </select>
      )}
      {pick && !card && !error && <p className="empty">{GLYPH.progress} Loading the shell</p>}
      {card && <Card card={card} />}
    </Panel>
  );
}

export function Build() {
  const [site, setSite] = useState<GuideSite | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api.guide().then(
      (s) => {
        if (!live) return;
        setSite(s);
        setPage(firstPage(s.pages));
      },
      (e: unknown) => {
        if (live) setError(e instanceof ApiError ? e.message : String(e));
      },
    );
    return () => {
      live = false;
    };
  }, []);

  return (
    <div className="view-build" data-testid="view-build">
      <div className="view-head">
        <h1 className="page-title">Build</h1>
        <p className="lede">How do I build this multi? The 3D guide from your Lab export.</p>
      </div>
      {error && <p className="empty">{GLYPH.warn} {error}</p>}
      {!site && !error && <p className="empty">{GLYPH.progress} Looking for the guide export</p>}
      {site && !site.pages.length && (
        <Panel label="3D build guide" testId="guide-empty">
          <span className="gos-label">{GLYPH.todo} No guide export yet</span>
          <p className="gos-sub">
            {site.present
              ? "The guide folder has no page with a 3D scene."
              : "Export the guide in a Lab client (/guidenhc exportsite) and copy the folder to GOS_HOME/data/guide-site."}
          </p>
        </Panel>
      )}
      {site && page && (
        <Panel
          label="3D build guide"
          testId="guide"
          aside={
            <span className="gos-src">
              {site.pages.length} {site.pages.length === 1 ? "page" : "pages"} · {site.files} files · {mb(site.bytes)}
            </span>
          }
        >
          {site.pages.length > 1 && (
            <select
              className="ask-input base-select guide-pick"
              aria-label="Guide page"
              value={page}
              onChange={(e) => setPage(e.target.value)}
              data-testid="guide-pick"
            >
              {site.pages.map((p) => (
                <option key={p.path} value={p.path}>
                  {p.title || p.path}
                </option>
              ))}
            </select>
          )}
          <iframe
            className="guide-frame"
            title="3D build guide"
            src={guideSrc(page)}
            sandbox="allow-scripts allow-same-origin"
            referrerPolicy="no-referrer"
            data-testid="guide-frame"
          />
        </Panel>
      )}
      <Shells />
    </div>
  );
}
