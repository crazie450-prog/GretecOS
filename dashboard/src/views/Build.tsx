// Build (plan 7.10, 7.14): "How do I build this multi?"
// WO-10.8: the 3D build guide (BuildGuide.tsx) draws a footprint from the core (positions from the
// footprint library, names and colours from the block catalog): turn, zoom, hover, layers,
// materials per layer, checklist, hatch slots and the BOM. Opened from the picker here, from a
// plan's machines (Designer) and from My base (Base), as #/build?guide=<registry name>@<meta>.
// WO-10.1's frame of Clayton's GuideNH export stays beside it for the in-game look; WO-10.8 serves
// it from its own origin with a path token (gregtec.build.guide, D304, D319), so its scripts can't
// reach the dashboard. On the phone's tailnet name there is no guide host, so no frame.
// WO-10.7 adds the decorative shells: the core designs and checks them (gregtec.build.shell).
// WO-10.10 replaces their list and quarter-turn card here with the shell workbench
// (ShellWorkbench.tsx): words, theme and sizes, Design and Revise as jobs, the versions with
// undo and compare, and the guide's 3D drawing with the machines and keep-outs as ghosts.
// WO-10.11 adds the bake-off panel under it (ShellBakeoff.tsx): several models' answers to one
// brief, blind, with his pick per case; it shows only once a run exists.
// Built to the design system: needs Clayton's board approval (D92, D158 (5)).
import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type { Facing, GuideEntry, GuideQuery, GuideSite } from "../api/types";
import { Panel } from "../components/core";
import { GLYPH } from "../state/states";
import { useApp } from "../state/store";
import { GuideView } from "./BuildGuide";
import { ShellBakeoff } from "./ShellBakeoff";
import { ShellWorkbench } from "./ShellWorkbench";

const mb = (bytes: number): string => `${(bytes / 1_000_000).toFixed(1)} MB`;
const FACINGS: readonly string[] = ["north", "east", "south", "west"];
const REFRESH_BEFORE_S = 30 * 60; // ask for a new frame token this long before it runs out

/** The frame's address for one page of the export: the base from the core (the guide host and
 * a path token) and the page's path, each segment encoded. */
export function guideSrc(base: string, path: string): string {
  return `${base}${path.split("/").map(encodeURIComponent).join("/")}`;
}

/** The page the view opens first: one of GOS's own guide (`gos/...`, the Lab steps' spike page) if
 * the export has one, else the first scene page. A full export holds every guide in the pack. */
export function firstPage(pages: GuideSite["pages"]): string | null {
  return (pages.find((p) => p.path.startsWith("gos/")) ?? pages[0])?.path ?? null;
}

/** The guide a `#/build?guide=...` address asks for, or null. */
export function guideFromHash(hash: string): GuideQuery | null {
  const at = hash.indexOf("?");
  if (at < 0) return null;
  const p = new URLSearchParams(hash.slice(at + 1));
  const controller = p.get("guide");
  if (!controller) return null;
  const facing = p.get("facing");
  const tiers: Record<string, number> = {};
  for (const t of p.getAll("tier")) {
    const [ch, v] = t.split("=");
    if (ch && v !== undefined && /^-?\d+$/.test(v)) tiers[ch] = Number(v);
  }
  return {
    controller,
    variant: p.get("variant"),
    facing: facing && FACINGS.includes(facing) ? (facing as Facing) : null,
    tiers,
  };
}

/** The Build view's address for a guide (what the Designer and My base link to). */
export function guideHash(q: GuideQuery): string {
  const p = new URLSearchParams({ guide: q.controller });
  if (q.variant) p.set("variant", q.variant);
  if (q.facing) p.set("facing", q.facing);
  for (const [ch, v] of Object.entries(q.tiers ?? {})) p.append("tier", `${ch}=${v}`);
  return `#/build?${p.toString()}`;
}

/** The controllers with a footprint, once per page load: the Designer and My base show a "Build
 * guide" link only for those (by GT mte id). */
let guidesOnce: Promise<GuideEntry[]> | null = null;
export function loadGuides(): Promise<GuideEntry[]> {
  guidesOnce ??= api.guides().then(
    (r) => r.guides,
    (e: unknown) => {
      guidesOnce = null; // try again next time
      throw e;
    },
  );
  return guidesOnce;
}

export function useGuides(): GuideEntry[] {
  const [list, setList] = useState<GuideEntry[]>([]);
  useEffect(() => {
    let live = true;
    loadGuides().then(
      (g) => live && setList(g),
      () => undefined, // no DB or no footprints: no links
    );
    return () => {
      live = false;
    };
  }, []);
  return list;
}

/** The guide's controller for a GT mte id, if it has a footprint. */
export function guideFor(guides: GuideEntry[], mte: number | null | undefined): string | null {
  if (mte === null || mte === undefined) return null;
  return guides.find((g) => g.mte === mte)?.controller ?? null;
}

/** Clayton's GuideNH export, framed from the guide's own origin (WO-10.1, D304, D319). */
function GuideFrame() {
  const [site, setSite] = useState<GuideSite | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState<string | null>(null);
  const expires = useRef<number | null>(null);
  const renew = useRef<(() => void) | null>(null);
  const socket = useApp((s) => s.socket);
  const wasOpen = useRef(false);
  useEffect(() => {
    // The token's key lives only in the core's memory: after a restart (the socket drops and
    // opens again) every token is refused, whatever its expiry, so ask for a new one (review L2).
    if (socket !== "open") return;
    if (wasOpen.current) renew.current?.();
    wasOpen.current = true;
  }, [socket]);
  useEffect(() => {
    let live = true;
    const load = (first: boolean) =>
      api.guide().then(
        (s) => {
          if (!live) return;
          setSite(s);
          expires.current = s.frame?.expires ?? null;
          if (first) setPage(firstPage(s.pages));
        },
        (e: unknown) => {
          if (live) setError(e instanceof ApiError ? e.message : String(e));
        },
      );
    void load(true);
    renew.current = () => void load(false);
    // The path token runs out (gregtec.build.guide.TOKEN_TTL_S): ask for a new one in time.
    const timer = window.setInterval(() => {
      if (expires.current !== null && expires.current - Date.now() / 1000 < REFRESH_BEFORE_S) void load(false);
    }, 5 * 60_000);
    return () => {
      live = false;
      renew.current = null;
      window.clearInterval(timer);
    };
  }, []);
  if (error) return <p className="empty">{GLYPH.warn} {error}</p>;
  if (!site) return <p className="empty">{GLYPH.progress} Looking for the guide export</p>;
  if (!site.pages.length)
    return (
      <Panel label="In-game look (GuideNH export)" testId="guide-empty">
        <span className="gos-label">{GLYPH.todo} No guide export yet</span>
        <p className="gos-sub">
          {site.present
            ? "The guide folder has no page with a 3D scene."
            : "Export the guide in a Lab client (/guidenhc exportsite) and copy the folder to GOS_HOME/data/guide-site."}
        </p>
      </Panel>
    );
  return (
    <Panel
      label="In-game look (GuideNH export)"
      testId="guide"
      aside={
        <span className="gos-src">
          {site.pages.length} {site.pages.length === 1 ? "page" : "pages"} · {site.files} files · {mb(site.bytes)}
        </span>
      }
    >
      {site.pages.length > 1 && page && (
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
      {site.frame && page ? (
        <iframe
          className="guide-frame"
          title="GuideNH export"
          src={guideSrc(site.frame.base, page)}
          sandbox="allow-scripts allow-same-origin"
          referrerPolicy="no-referrer"
          data-testid="guide-frame"
        />
      ) : (
        <p className="gos-sub" data-testid="guide-frame-pc-only">
          {GLYPH.todo} The in-game look opens on the PC's dashboard only; the guide above works here.
        </p>
      )}
    </Panel>
  );
}

export function Build() {
  const [query, setQuery] = useState<GuideQuery | null>(() => guideFromHash(window.location.hash));
  const guides = useGuides();
  useEffect(() => {
    const on = () => setQuery(guideFromHash(window.location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const open = (q: GuideQuery) => {
    window.location.hash = guideHash(q).slice(1);
  };
  return (
    <div className="view-build" data-testid="view-build">
      <div className="view-head">
        <h1 className="page-title">Build</h1>
        <p className="lede">How do I build this multi? Turn it, step through the layers, tick them off.</p>
      </div>
      <Panel
        label="Multiblock"
        testId="guide-picker"
        aside={<span className="gos-src">{guides.length} with a footprint</span>}
      >
        {guides.length ? (
          <select
            className="ask-input base-select"
            aria-label="Multiblock"
            value={query?.controller ?? ""}
            onChange={(e) => e.target.value && open({ controller: e.target.value })}
            data-testid="guide-choose"
          >
            <option value="">Pick a multiblock</option>
            {guides.map((g) => (
              <option key={g.controller} value={g.controller}>
                {g.name ?? g.controller}
                {g.mte !== null ? ` (mte:${g.mte})` : ""}
              </option>
            ))}
          </select>
        ) : (
          <p className="gos-sub" data-testid="guide-none">
            {GLYPH.todo} No footprints yet: gos structures load (base pack) or gos footprints capture (addons, in the Lab).
          </p>
        )}
      </Panel>
      {query && <GuideView query={query} onQuery={open} />}
      <GuideFrame />
      <ShellWorkbench />
      <ShellBakeoff />
    </div>
  );
}
