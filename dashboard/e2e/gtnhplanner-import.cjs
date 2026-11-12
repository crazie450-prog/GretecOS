// Opens gtnhplanner.com project files made by `gos plan --save` (WO-3.6, D66, D202) in the live
// site and checks what its board shows. Not part of the Playwright suite (it needs the internet):
//
//   node e2e/gtnhplanner-import.cjs <gtnhplanner.json> ...
//
// For each file: a fresh page, the site's own "open plan" file input (the button that opens a
// plan file as a new design tab), then the overview panel is read. It passes when the board has
// one card per plan node and drawer, the design tab carries the plan's name, the overview has a
// MACHINES total and every input and output drawer is listed. Screenshots land beside each file
// (<file>.site.png). Behind a proxy, HTTPS_PROXY is used.
const { chromium } = require("@playwright/test");
const fs = require("node:fs");
const path = require("node:path");

const SITE = process.env.GTNHPLANNER_URL || "https://gtnhplanner.com/";

async function check(browser, file) {
  const project = JSON.parse(fs.readFileSync(file, "utf8"));
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(SITE, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.locator('input[type="file"][accept*="json"]').first().waitFor({ state: "attached", timeout: 60000 });
  await page.waitForLoadState("networkidle", { timeout: 60000 }).catch(() => {});
  for (const name of ["Dismiss", "Not now"]) {
    const b = page.getByRole("button", { name, exact: true });
    if (await b.count()) await b.first().click().catch(() => {});
  }
  await page.locator('input[type="file"][accept*="json"]').first().setInputFiles(path.resolve(file));
  const want = project.nodes.length + project.storages.length;
  try {
    await page.waitForFunction((n) => document.querySelectorAll(".react-flow__node").length >= n, want, { timeout: 60000 });
  } catch {
    // Read what the board shows anyway: the problems below say what is missing.
  }
  await page.waitForTimeout(3000);
  for (const name of ["Dismiss", "Not now"]) {
    const b = page.getByRole("button", { name, exact: true });
    if (await b.count()) await b.first().click().catch(() => {});
  }
  const cards = await page.locator(".react-flow__node").count();
  const text = await page.locator("body").innerText();
  const machines = project.nodes.reduce((s, n) => s + n.machineCount, 0);
  const m = /MACHINES\s+(\d+)/.exec(text);
  const problems = [];
  if (cards !== want) problems.push(`board shows ${cards} cards, the file has ${want}`);
  if (!m) problems.push("the overview has no MACHINES total");
  for (const s of project.storages) {
    if (!text.includes(s.displayName)) problems.push(`drawer ${s.displayName} not shown`);
  }
  if (!text.includes(project.name)) problems.push(`tab ${project.name} not shown`);
  for (const e of errors) problems.push(`page error: ${e}`);
  await page.screenshot({ path: file.replace(/\.json$/, "") + ".site.png" });
  await page.close();
  // The site sizes machines with its own overclock math (no machine bonuses), so its MACHINES
  // total is reported beside the file's, not compared (D202).
  return { file, cards, site_machines: m ? Number(m[1]) : null, file_machines: machines, problems, errors };
}

(async () => {
  const proxy = process.env.HTTPS_PROXY ? { server: process.env.HTTPS_PROXY } : undefined;
  const browser = await chromium.launch({ proxy });
  let failed = 0;
  for (const f of process.argv.slice(2)) {
    const r = await check(browser, f);
    failed += r.problems.length ? 1 : 0;
    console.log(JSON.stringify(r));
  }
  await browser.close();
  process.exit(failed ? 1 : 0);
})().catch((e) => {
  console.error("FAILED", e);
  process.exit(2);
});
