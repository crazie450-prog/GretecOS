// WCAG contrast from the design tokens (design system README: "Contrast"): colours as tokens.json
// writes them (#rrggbb or rgba()), translucent ones composited over what they sit on.
export type RGBA = [number, number, number, number];

export function parseColor(value: string): RGBA {
  const v = value.trim();
  const hex = /^#([0-9a-f]{6})$/i.exec(v);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255, 1];
  }
  const fn = /^rgba?\(([^)]+)\)$/i.exec(v);
  if (fn) {
    const parts = fn[1].split(/[\s,/]+/).filter(Boolean).map((p) => (p.endsWith("%") ? (parseFloat(p) / 100) * 255 : parseFloat(p)));
    const alpha = parts.length > 3 ? (fn[1].includes("%") && parts[3] > 1 ? parts[3] / 255 : parts[3]) : 1;
    return [parts[0], parts[1], parts[2], alpha];
  }
  const srgb = /^color\(srgb\s+([\d.e-]+)\s+([\d.e-]+)\s+([\d.e-]+)(?:\s*\/\s*([\d.e-]+))?\)$/i.exec(v);
  if (srgb) {
    return [Number(srgb[1]) * 255, Number(srgb[2]) * 255, Number(srgb[3]) * 255, srgb[4] === undefined ? 1 : Number(srgb[4])];
  }
  throw new Error(`not a colour: ${value}`);
}

export function over(fg: RGBA, bg: RGBA): RGBA {
  const a = fg[3];
  return [fg[0] * a + bg[0] * (1 - a), fg[1] * a + bg[1] * (1 - a), fg[2] * a + bg[2] * (1 - a), 1];
}

function channel(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

export function luminance(c: RGBA): number {
  return 0.2126 * channel(c[0]) + 0.7152 * channel(c[1]) + 0.0722 * channel(c[2]);
}

export function ratio(a: RGBA, b: RGBA): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** Contrast of `fg` on `bg`, where `bg` may itself be translucent over `base`. */
export function contrastOn(fg: string, bg: string, base: string): number {
  return contrastOver(fg, base, bg);
}

/** Contrast of `fg` on a stack of surfaces, bottom first (each may be translucent). */
export function contrastOver(fg: string, ...layers: string[]): number {
  let ground: RGBA = [0, 0, 0, 1];
  for (const layer of layers) ground = over(parseColor(layer), ground);
  return ratio(over(parseColor(fg), ground), ground);
}

/** Distance between two colours (Euclidean in sRGB, 0 to about 441). */
export function distance(a: string, b: string): number {
  const x = parseColor(a);
  const y = parseColor(b);
  return Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]);
}
