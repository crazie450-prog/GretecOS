// Each theme's core art (age_themes.md, design system: iconography): drawn shapes in the theme's
// and tier's colours, showing the voice state (standby, listening, thinking, speaking). It moves
// only while a voice state is active, never at rest, at most 30 frames a second, and not at all
// with "Pause motion" (plan 7.14 rule 6).
import { useEffect, useRef } from "react";
import { GLANCE_FPS, frameDue } from "../state/mode";
import type { Slug } from "../theme/themes";
import { theme } from "../theme/themes";

export type VoiceLook = "standby" | "listening" | "thinking" | "speaking" | "off";

function useFrames(active: boolean, draw: (t: number) => void): void {
  const drawRef = useRef(draw);
  drawRef.current = draw;
  useEffect(() => {
    if (!active) {
      drawRef.current(0);
      return;
    }
    let last = -Infinity;
    let frame = 0;
    const loop = (now: number): void => {
      if (frameDue(last, now, GLANCE_FPS)) {
        last = now;
        drawRef.current(now);
      }
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
  }, [active]);
}

const NEEDLE: Record<VoiceLook, number> = { off: -120, standby: -90, listening: -20, thinking: 30, speaking: 80 };

function Art({ slug, state }: { slug: Slug; state: VoiceLook }) {
  const tier = `var(--${theme(slug).tierColor})`;
  const stroke = "var(--shell-text-2)";
  switch (slug) {
    case "brass":
      return (
        <g>
          <circle r="44" fill="none" stroke="var(--accent)" strokeWidth="4" />
          {Array.from({ length: 9 }, (_, i) => -120 + i * 30).map((a) => (
            <line key={a} x1="0" y1="-38" x2="0" y2="-30" stroke={stroke} strokeWidth="3" transform={`rotate(${a})`} />
          ))}
          <line className="needle" x1="0" y1="6" x2="0" y2="-34" stroke={tier} strokeWidth="4" strokeLinecap="round" transform={`rotate(${NEEDLE[state]})`} />
          <circle r="6" fill="var(--accent)" />
        </g>
      );
    case "machine":
      return (
        <g>
          {[-3, -2, -1, 0, 1, 2].flatMap((x) =>
            [-3, -2, -1, 0, 1, 2]
              .filter((y) => (x + 0.5) ** 2 + (y + 0.5) ** 2 <= 10)
              .map((y) => <rect key={`${x},${y}`} x={x * 12} y={y * 12} width="11" height="11" fill={tier} opacity={(x + y) % 2 === 0 ? 1 : 0.75} />),
          )}
        </g>
      );
    case "alu":
      return (
        <g>
          <circle r="44" fill="none" stroke="var(--tier-mv)" strokeWidth="6" />
          <circle r="34" fill="none" stroke={stroke} strokeWidth="2" strokeDasharray="2 5" />
          <line className="needle" x1="0" y1="0" x2="0" y2="-28" stroke="var(--accent)" strokeWidth="4" transform={`rotate(${NEEDLE[state]})`} />
          <circle r="5" fill={stroke} />
        </g>
      );
    case "hud":
      return (
        <g>
          {[40, 30, 20].map((r, i) =>
            Array.from({ length: 12 - i * 3 }, (_, k) => (360 / (12 - i * 3)) * k).map((a) => (
              <circle key={`${r}-${a}`} r="2.6" cx="0" cy={-r} fill={tier} transform={`rotate(${a})`} />
            )),
          )}
          <circle r="8" fill={tier} />
        </g>
      );
    case "circuit":
      return (
        <g>
          <rect x="-30" y="-30" width="60" height="60" fill="none" stroke="var(--circuit-pin)" strokeWidth="3" />
          {[-20, -10, 0, 10, 20].map((p) => (
            <g key={p} stroke="var(--circuit-pin)" strokeWidth="3">
              <line x1={p} y1="-30" x2={p} y2="-42" />
              <line x1={p} y1="30" x2={p} y2="42" />
              <line x1="-30" y1={p} x2="-42" y2={p} />
              <line x1="30" y1={p} x2="42" y2={p} />
            </g>
          ))}
          <rect x="-14" y="-14" width="28" height="28" fill="var(--tier-edge)" />
        </g>
      );
    case "tung":
      return (
        <g>
          <polygon points="0,-44 38,-22 38,22 0,44 -38,22 -38,-22" fill="none" stroke="var(--tung-bolt)" strokeWidth="5" />
          <circle r="18" fill="var(--tier-iv)" />
          <circle r="18" fill="none" stroke={stroke} strokeWidth="2" />
        </g>
      );
    case "holo":
      return (
        <g fill="none" stroke="var(--holo-glow)" strokeWidth="2">
          <ellipse rx="40" ry="14" />
          <ellipse rx="40" ry="14" transform="rotate(60)" />
          <ellipse rx="40" ry="14" transform="rotate(-60)" />
          <line x1="0" y1="40" x2="0" y2="48" />
        </g>
      );
    case "fusion":
      return (
        <g fill="none">
          <ellipse rx="42" ry="20" stroke="var(--tier-zpm)" strokeWidth="6" />
          <ellipse rx="26" ry="10" stroke="var(--fusion-plasma-blue)" strokeWidth="3" />
        </g>
      );
    case "naq":
      return (
        <g>
          <polygon points="0,-44 18,-6 0,30 -18,-6" fill="var(--tier-uv)" opacity="0.85" />
          <line x1="-34" y1="42" x2="34" y2="42" stroke="var(--accent)" strokeWidth="3" />
        </g>
      );
    case "deep":
      return (
        <g>
          <ellipse rx="44" ry="12" fill="none" stroke="var(--deep-nebula)" strokeWidth="5" />
          <circle r="11" fill="var(--bg)" stroke="var(--accent)" strokeWidth="2" />
        </g>
      );
    case "calm":
      return (
        <g fill="none" stroke="var(--accent)">
          <circle r="42" strokeWidth="2" opacity="0.5" />
          <circle r="30" strokeWidth="3" opacity="0.75" />
          <circle r="16" strokeWidth="5" />
        </g>
      );
    case "manual":
      return (
        <g>
          <circle r="42" fill="none" stroke="var(--text)" strokeWidth="3" />
          <circle r="34" fill="none" stroke="var(--text)" strokeWidth="1" strokeDasharray="3 3" />
          <text textAnchor="middle" dy="6" fontSize="18" fill="var(--text)" fontFamily="var(--font-mono)">
            GOS
          </text>
        </g>
      );
  }
}

/** The core art with its standby ring; `size` in px. */
export function CoreArt(props: { slug: Slug; state: VoiceLook; size: number; motion: boolean }) {
  const pulse = useRef<SVGGElement>(null);
  const active = props.motion && (props.state === "listening" || props.state === "thinking" || props.state === "speaking");
  useFrames(active, (t) => {
    const g = pulse.current;
    if (!g) return;
    const k = active ? 1 + 0.04 * Math.sin(t / 220) : 1;
    g.setAttribute("transform", `scale(${k.toFixed(3)})`);
  });
  return (
    <svg
      className={`core-art art-${props.slug} voice-${props.state}`}
      width={props.size}
      height={props.size}
      viewBox="-56 -56 112 112"
      role="img"
      aria-label={`voice ${props.state}`}
      data-testid="core-art"
    >
      <circle className="standby-ring" r="52" fill="none" strokeWidth="3" />
      <g ref={pulse}>
        <Art slug={props.slug} state={props.state} />
      </g>
    </svg>
  );
}
