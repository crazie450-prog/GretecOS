// A stand-in for GuideNH's model viewer (WO-10.1), written for this test: it takes the same path
// through the browser as GuideNH's viewer (a module script, a fetch of a .scene.gz under _res/,
// gunzip with DecompressionStream, a canvas, a layer control, hover) and draws plain isometric
// cubes. The scene is JSON here, not GuideME's FlatBuffers ExpScene. It also tries to reach /api,
// which the guide frame's CSP must refuse, and says on <body> what happened.
const body = document.body;

async function gunzip(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

async function probeApi() {
  try {
    await fetch("/api/state", { credentials: "same-origin" });
    body.dataset.apiBlocked = "0";
  } catch {
    body.dataset.apiBlocked = "1";
  }
}

const COLOURS = ["#7d8a8f", "#b5793a", "#4f8fbf", "#8fbf4f", "#bf4f8f"];

function draw(canvas, blocks, layer, keys) {
  const ctx = canvas.getContext("2d");
  const s = 22;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const shown = blocks
    .filter((b) => b.pos[1] <= layer)
    .sort((a, b) => a.pos[0] + a.pos[2] + a.pos[1] - (b.pos[0] + b.pos[2] + b.pos[1]));
  const boxes = [];
  for (const b of shown) {
    const [x, y, z] = b.pos;
    const cx = canvas.width / 2 + (x - z) * s;
    const cy = canvas.height / 2 + (x + z) * s * 0.5 - y * s;
    ctx.fillStyle = COLOURS[keys.indexOf(b.block) % COLOURS.length];
    ctx.beginPath();
    ctx.moveTo(cx, cy - s * 0.5);
    ctx.lineTo(cx + s, cy);
    ctx.lineTo(cx, cy + s * 0.5);
    ctx.lineTo(cx - s, cy);
    ctx.closePath();
    ctx.fill();
    ctx.fillRect(cx - s, cy, 2 * s, s);
    boxes.push({ b, x0: cx - s, x1: cx + s, y0: cy - s * 0.5, y1: cy + s });
  }
  return boxes;
}

async function hydrate(img) {
  const url = new URL(img.dataset.sceneSrc, document.baseURI);
  const response = await fetch(url);
  if (!response.ok) throw new Error(`scene ${response.status}`);
  const scene = JSON.parse(new TextDecoder().decode(await gunzip(new Uint8Array(await response.arrayBuffer()))));
  const keys = [...new Set(scene.blocks.map((b) => b.block))];
  const top = Math.max(...scene.blocks.map((b) => b.pos[1]));
  const canvas = document.createElement("canvas");
  canvas.width = Number(img.getAttribute("width"));
  canvas.height = Number(img.getAttribute("height"));
  canvas.className = "game-scene";
  const slider = document.createElement("input");
  slider.type = "range";
  slider.min = "0";
  slider.max = String(top);
  slider.value = String(top);
  slider.setAttribute("aria-label", "layer");
  slider.dataset.testid = "layer";
  img.replaceWith(canvas);
  canvas.after(slider);
  const readout = document.querySelector("[data-testid=hover]");
  let boxes = draw(canvas, scene.blocks, top, keys);
  slider.addEventListener("input", () => {
    boxes = draw(canvas, scene.blocks, Number(slider.value), keys);
    body.dataset.layer = slider.value;
  });
  canvas.addEventListener("mousemove", (e) => {
    const r = canvas.getBoundingClientRect();
    const px = e.clientX - r.left;
    const py = e.clientY - r.top;
    const hit = [...boxes].reverse().find((h) => px >= h.x0 && px <= h.x1 && py >= h.y0 && py <= h.y1);
    readout.textContent = hit ? `${hit.b.block} at ${hit.b.pos.join(", ")}` : "hover a block";
  });
  body.dataset.blocks = String(scene.blocks.length);
  body.dataset.layer = String(top);
}

const scenes = [...document.querySelectorAll("img.game-scene[data-scene-src]")];
Promise.all([probeApi(), ...scenes.map(hydrate)]).then(
  () => {
    body.dataset.ready = "1";
  },
  (error) => {
    body.dataset.ready = "error";
    body.dataset.error = String(error);
  },
);
