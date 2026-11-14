// Build (plan 7.10, 7.14): "How do I build this multi?" WO-10.1's spike: the core serves Clayton's
// GuideNH export (GOS_HOME/data/guide-site, /guide/...) and this view frames one of its scene pages.
// The frame's own CSP keeps its scripts and fetches inside /guide/ (gregtec.build.guide, D304).
// WO-10.8 builds the real viewer UI. Built to the design system: needs Clayton's board approval.
import { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { GuideSite } from "../api/types";
import { Panel } from "../components/core";
import { GLYPH } from "../state/states";

const mb = (bytes: number): string => `${(bytes / 1_000_000).toFixed(1)} MB`;

/** The frame's address for one page of the export (each path segment encoded). */
export function guideSrc(path: string): string {
  return `/guide/${path.split("/").map(encodeURIComponent).join("/")}`;
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
        setPage(s.pages[0]?.path ?? null);
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
    </div>
  );
}
