// The shell viewer drawn with WebGL (WO-10.19, D433): the same Scene props, test ids and data
// attributes as the build guide's SVG drawing, but the faces are one vertex buffer built once per
// build (voxelMesh: only faces that can be seen, as one indexed vertex buffer), depth-tested, and a turn, zoom, layer or hover is
// a uniform and one draw call, not thousands of SVG polygons re-projected and re-sorted (D388: the
// bake-off lagged at 10k-25k blocks). Written straight against WebGL2: no new dependency. It draws
// only when something changes (a drag is still one setView a frame), and each viewer owns its own
// context, freed on unmount, so the bake-off's side-by-side viewers don't pile contexts up. The
// ghosts (machines, keep-outs) stay an SVG overlay fitted to the same box, so their dashes, token
// colours and tooltips are the CSS's. No WebGL2 (jsdom, old GPUs, a context refused): the SVG Scene.
// Colours: each block's own, and --bg / --primary read from the theme. Built to the design
// system: needs Clayton's board approval (D92).
import { useEffect, useMemo, useRef, useState } from "react";
import { Scene, SceneChrome, type SceneProps, useSceneView } from "./BuildGuide";
import { boxEdges, structureBox, zoomed } from "./guideMesh";
import { FACE_STRIDE, buildMesh, facesAt, pick, vertices, viewMatrix } from "./voxelMesh";

const hasWebGL2 = (): boolean => typeof window !== "undefined" && typeof window.WebGL2RenderingContext !== "undefined";

/** The shell's 3D drawing: WebGL2 when the browser has it, else the SVG Scene. */
export function GLScene(props: SceneProps) {
  const [failed, setFailed] = useState(() => !hasWebGL2());
  if (failed) return <Scene {...props} />;
  return <GLSceneInner {...props} onFail={() => setFailed(true)} />;
}

const VERT = `#version 300 es
layout(location = 0) in uvec3 aPos;
layout(location = 1) in uvec2 aDirFlags;
layout(location = 2) in vec4 aRgb;
layout(location = 3) in uint aCell;
uniform mat4 uView;
uniform int uUpTo;
flat out vec3 vRgb;
flat out float vShade;
flat out int vCell;
flat out int vY;
out vec2 vUv;
void main() {
  int d = int(aDirFlags.x); // E, W, Up, Down, S, N
  int y = int(aPos.y);
  bool covered = (aDirFlags.y & 1u) != 0u;
  if (y > uUpTo || (covered && y != uUpTo)) {
    gl_Position = vec4(2.0, 2.0, 2.0, 1.0); // above the cut, or under a block: off screen
    return;
  }
  int k = int(aDirFlags.y >> 1u); // the corner, 0..3 round the face
  vec2 uv = vec2(k == 1 || k == 2 ? 1.0 : 0.0, k >= 2 ? 1.0 : 0.0);
  float side = (d & 1) == 0 ? 1.0 : 0.0; // the face's own plane: the cell's high or low side
  int axis = d >> 1;
  vec3 corner = axis == 0 ? vec3(side, uv) : (axis == 1 ? vec3(uv.x, side, uv.y) : vec3(uv, side));
  gl_Position = uView * vec4(vec3(aPos) + corner, 1.0);
  vUv = uv;
  vRgb = aRgb.rgb;
  // the SVG's fill-opacity per face: x sides .85, z sides .7, top and bottom 1
  vShade = axis == 0 ? 0.85 : (axis == 2 ? 0.7 : 1.0);
  vCell = int(aCell);
  vY = y;
}`;

const FRAG = `#version 300 es
precision highp float;
precision highp int;
uniform vec3 uBg;
uniform vec3 uPrimary;
uniform int uHover;
uniform int uUpTo;
flat in vec3 vRgb;
flat in float vShade;
flat in int vCell;
flat in int vY;
in vec2 vUv;
out vec4 outColour;
void main() {
  bool hov = vCell == uHover;
  // the SVG's look, opaque: fill-opacity, and the layers under the cut at half (.below)
  float a = vShade * ((vY == uUpTo || hov) ? 1.0 : 0.5);
  vec3 col = mix(uBg, vRgb, a);
  // the face's edge: the SVG's 0.03 stroke in --bg, the hovered block's 0.12 in --primary
  float e = min(min(vUv.x, 1.0 - vUv.x), min(vUv.y, 1.0 - vUv.y));
  float w = fwidth(e);
  float edge = hov ? 0.06 : 0.015;
  float line = 1.0 - smoothstep(edge - w, edge + w, e);
  col = mix(col, hov ? uPrimary : uBg, line);
  outColour = vec4(col, 1.0);
}`;

type RGB = [number, number, number];

/** A computed CSS colour (rgb(), rgba() or color(srgb ...)) as 0..1. */
function parseColour(v: string): RGB {
  const rgb = /^rgba?\(([^)]+)\)$/.exec(v.trim());
  if (rgb) {
    const p = rgb[1].split(/[\s,/]+/).filter(Boolean).map(Number);
    return [p[0] / 255, p[1] / 255, p[2] / 255];
  }
  const srgb = /^color\(srgb ([\d.e-]+) ([\d.e-]+) ([\d.e-]+)/.exec(v.trim());
  if (srgb) return [Number(srgb[1]), Number(srgb[2]), Number(srgb[3])];
  return [0, 0, 0];
}

let software: boolean | null = null;

/** Whether WebGL runs on the CPU (SwiftShader, llvmpipe): there 4x multisampling costs about four
 * times the frame (measured: ~175 ms against ~45 ms a turn at 1700x820), so it is left off; a
 * real GPU gets it. Asked once per page, on a throwaway context. */
function softwareGpu(): boolean {
  if (software !== null) return software;
  const gl = document.createElement("canvas").getContext("webgl2");
  const info = gl?.getExtension("WEBGL_debug_renderer_info");
  const name = gl ? String(gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : gl.RENDERER)) : "";
  gl?.getExtension("WEBGL_lose_context")?.loseContext();
  software = /swiftshader|llvmpipe|softpipe|software/i.test(name);
  return software;
}

interface GL {
  gl: WebGL2RenderingContext;
  prog: WebGLProgram;
  vao: WebGLVertexArrayObject;
  buf: WebGLBuffer;
  idx: WebGLBuffer;
  u: Record<"view" | "upTo" | "bg" | "primary" | "hover", WebGLUniformLocation | null>;
  count: number; // indices in the buffer (six per face)
}

function compile(gl: WebGL2RenderingContext, type: number, src: string): WebGLShader | null {
  const s = gl.createShader(type);
  if (!s) return null;
  gl.shaderSource(s, src);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
    console.warn("GLScene shader:", gl.getShaderInfoLog(s));
    gl.deleteShader(s);
    return null;
  }
  return s;
}

/** The program, the VAO and its vertex and index buffers on ``gl``, or null when the GPU won't. */
function setUp(gl: WebGL2RenderingContext): GL | null {
  const vs = compile(gl, gl.VERTEX_SHADER, VERT);
  const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG);
  const prog = gl.createProgram();
  if (!vs || !fs || !prog) return null;
  gl.attachShader(prog, vs);
  gl.attachShader(prog, fs);
  gl.linkProgram(prog);
  gl.deleteShader(vs);
  gl.deleteShader(fs);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
    console.warn("GLScene program:", gl.getProgramInfoLog(prog));
    gl.deleteProgram(prog);
    return null;
  }
  const vao = gl.createVertexArray();
  const buf = gl.createBuffer();
  const idx = gl.createBuffer();
  if (!vao || !buf || !idx) return null;
  gl.bindVertexArray(vao);
  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, idx);
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribIPointer(0, 3, gl.UNSIGNED_SHORT, FACE_STRIDE, 0);
  gl.enableVertexAttribArray(1);
  gl.vertexAttribIPointer(1, 2, gl.UNSIGNED_BYTE, FACE_STRIDE, 6);
  gl.enableVertexAttribArray(2);
  gl.vertexAttribPointer(2, 4, gl.UNSIGNED_BYTE, true, FACE_STRIDE, 8);
  gl.enableVertexAttribArray(3);
  gl.vertexAttribIPointer(3, 1, gl.UNSIGNED_INT, FACE_STRIDE, 12);
  gl.bindVertexArray(null);
  const at = (n: string) => gl.getUniformLocation(prog, n);
  return {
    gl,
    prog,
    vao,
    buf,
    idx,
    u: { view: at("uView"), upTo: at("uUpTo"), bg: at("uBg"), primary: at("uPrimary"), hover: at("uHover") },
    count: 0,
  };
}

function upload(g: GL, mesh: { data: ArrayBuffer; indices: Uint32Array }): void {
  const { gl } = g;
  gl.bindBuffer(gl.ARRAY_BUFFER, g.buf);
  gl.bufferData(gl.ARRAY_BUFFER, mesh.data, gl.STATIC_DRAW);
  gl.bindVertexArray(g.vao);
  gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, mesh.indices, gl.STATIC_DRAW);
  gl.bindVertexArray(null);
  g.count = mesh.indices.length;
}

function GLSceneInner(props: SceneProps & { onFail: () => void }) {
  const { cells, size, upTo, palette } = props;
  const prefix = props.prefix ?? "guide";
  const wrap = useRef<HTMLDivElement | null>(null);
  const host = useRef<HTMLDivElement | null>(null);
  const probe = useRef<HTMLSpanElement | null>(null);
  const glRef = useRef<GL | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const { view, setView, zoom, setZoom, turnBy } = useSceneView(wrap);
  const [hover, setHover] = useState<number | null>(null);
  const [gen, setGen] = useState(0); // bumped when a context is (re)made
  const [px, setPx] = useState<{ w: number; h: number }>({ w: 0, h: 0 }); // the canvas in device pixels
  const [tokens, setTokens] = useState<{ bg: RGB; primary: RGB }>({ bg: [0, 0, 0], primary: [1, 1, 1] });
  const drag = useRef<{ x: number; y: number; moved: number } | null>(null);
  const mesh = useMemo(() => buildMesh(cells, size, palette), [cells, size, palette]);
  const gpu = useMemo(() => vertices(mesh), [mesh]);
  const box = zoomed(structureBox(size, view), zoom);
  const { onFail } = props;

  // The context: its own canvas per mount (StrictMode mounts twice; a lost context can't come back
  // on the same canvas), freed on unmount.
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const canvas = document.createElement("canvas");
    canvas.setAttribute("aria-hidden", "true");
    el.appendChild(canvas);
    canvasRef.current = canvas;
    const gl = canvas.getContext("webgl2", { antialias: !softwareGpu(), alpha: true, premultipliedAlpha: true });
    const made = gl ? setUp(gl) : null;
    if (!made) {
      el.removeChild(canvas);
      onFail();
      return;
    }
    glRef.current = made;
    setGen((n) => n + 1);
    const lost = (e: Event) => {
      e.preventDefault();
      glRef.current = null;
    };
    const restored = () => {
      const again = setUp(made.gl);
      glRef.current = again;
      if (again) setGen((n) => n + 1);
      else onFail();
    };
    canvas.addEventListener("webglcontextlost", lost);
    canvas.addEventListener("webglcontextrestored", restored);
    return () => {
      canvas.removeEventListener("webglcontextlost", lost);
      canvas.removeEventListener("webglcontextrestored", restored);
      const g = glRef.current;
      if (g) {
        g.gl.deleteBuffer(g.buf);
        g.gl.deleteBuffer(g.idx);
        g.gl.deleteVertexArray(g.vao);
        g.gl.deleteProgram(g.prog);
      }
      made.gl.getExtension("WEBGL_lose_context")?.loseContext();
      glRef.current = null;
      canvasRef.current = null;
      el.removeChild(canvas);
    };
  }, []);

  // The vertex and index buffers: once per build (and per new context).
  useEffect(() => {
    if (glRef.current) upload(glRef.current, gpu);
  }, [gpu, gen]);

  // The canvas's size in device pixels: on resize, and when the window moves to another screen.
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const measure = (entry?: ResizeObserverEntry) => {
      const dpr = window.devicePixelRatio || 1;
      const dev = entry?.devicePixelContentBoxSize?.[0];
      const w = dev ? dev.inlineSize : Math.round(el.clientWidth * dpr);
      const h = dev ? dev.blockSize : Math.round(el.clientHeight * dpr);
      setPx((p) => (p.w === w && p.h === h ? p : { w, h }));
    };
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver((es) => measure(es[0])) : null;
    try {
      ro?.observe(el, { box: "device-pixel-content-box" });
    } catch {
      ro?.observe(el);
    }
    let mq: MediaQueryList | null = null;
    const onDpr = () => {
      measure();
      mq?.removeEventListener("change", onDpr);
      mq = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
      mq.addEventListener("change", onDpr);
    };
    onDpr();
    return () => {
      ro?.disconnect();
      mq?.removeEventListener("change", onDpr);
    };
  }, []);

  // The theme's --bg and --primary, read again when the theme class changes.
  useEffect(() => {
    const read = () => {
      const p = probe.current;
      if (!p) return;
      p.style.color = "var(--bg)";
      const bg = parseColour(getComputedStyle(p).color);
      p.style.color = "var(--primary)";
      const primary = parseColour(getComputedStyle(p).color);
      setTokens((t) => (t.bg.join() === bg.join() && t.primary.join() === primary.join() ? t : { bg, primary }));
    };
    read();
    const mo = new MutationObserver(read);
    mo.observe(document.body, { attributes: true, attributeFilter: ["class"] });
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => mo.disconnect();
  }, []);

  // One draw per change: no loop.
  useEffect(() => {
    const g = glRef.current;
    const canvas = canvasRef.current;
    if (!g || !canvas || !px.w || !px.h) return;
    const { gl } = g;
    if (canvas.width !== px.w) canvas.width = px.w;
    if (canvas.height !== px.h) canvas.height = px.h;
    gl.viewport(0, 0, px.w, px.h);
    gl.clearColor(0, 0, 0, 0);
    gl.clearDepth(1);
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LESS);
    gl.disable(gl.CULL_FACE);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    if (!g.count) return;
    gl.useProgram(g.prog);
    gl.uniformMatrix4fv(g.u.view, false, viewMatrix(size, view, box, px.w, px.h));
    gl.uniform1i(g.u.upTo, upTo);
    gl.uniform1i(g.u.hover, hover ?? -1);
    gl.uniform3fv(g.u.bg, tokens.bg);
    gl.uniform3fv(g.u.primary, tokens.primary);
    gl.bindVertexArray(g.vao);
    gl.drawElements(gl.TRIANGLES, g.count, gl.UNSIGNED_INT, 0);
    gl.bindVertexArray(null);
  }, [mesh, gen, px, view, zoom, upTo, hover, tokens, size]);

  const cellAt = (clientX: number, clientY: number): number | null => {
    const r = (canvasRef.current ?? wrap.current)?.getBoundingClientRect();
    if (!r || !r.width || !r.height) return null;
    return pick(mesh, upTo, view, box, r.width, r.height, clientX - r.left, clientY - r.top);
  };

  return (
    <div className="guide-viewer">
      <div
        ref={wrap}
        className="guide-scene gl"
        role="img"
        aria-label={props.label}
        data-testid={`${prefix}-scene`}
        data-renderer="webgl"
        data-faces={facesAt(mesh, upTo)}
        data-yaw={Math.round(view.yaw)}
        data-zoom={zoom.toFixed(2)}
        data-ghosts={props.ghosts?.length ?? 0}
        onPointerDown={(e) => {
          drag.current = { x: e.clientX, y: e.clientY, moved: 0 };
          (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (d) {
            const dx = e.clientX - d.x;
            const dy = e.clientY - d.y;
            d.moved += Math.abs(dx) + Math.abs(dy);
            d.x = e.clientX;
            d.y = e.clientY;
            if (d.moved > 4) turnBy(dx, dy);
            return;
          }
          setHover(cellAt(e.clientX, e.clientY));
        }}
        onPointerUp={(e) => {
          const d = drag.current;
          drag.current = null;
          // a tap (the phone has no hover): the block under the finger
          if (d && d.moved <= 4) setHover(cellAt(e.clientX, e.clientY));
        }}
        onPointerLeave={() => {
          if (!drag.current) setHover(null);
        }}
      >
        <div ref={host} className="guide-gl" />
        <svg className="guide-overlay" viewBox={box.map((n) => n.toFixed(3)).join(" ")} aria-hidden="true">
          {(props.ghosts ?? []).map((g) => (
            <path key={`${g.kind}-${g.id}`} className={`guide-ghost ${g.kind}`} d={boxEdges(size, g, view)} data-ghost={g.kind}>
              <title>{g.label}</title>
            </path>
          ))}
        </svg>
        <span ref={probe} hidden />
      </div>
      <SceneChrome
        prefix={prefix}
        compassLabel={props.compassLabel}
        line={hover === null ? props.hint : props.describe(hover)}
        view={view}
        setView={setView}
        setZoom={setZoom}
      />
    </div>
  );
}
