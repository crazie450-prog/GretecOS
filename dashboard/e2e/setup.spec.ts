// WO-2.9 acceptance, end to end: a fresh install is configured from the wizard alone. An empty
// GOS_HOME (serve_core.py --fresh) -> pair -> the wizard -> gos.toml written (config.py reads it
// back), the (fake) keyring holds the key and the token, and doctor passes on a fake Prism tree.
// Then the wizard re-opens from Settings to edit a setting. Screenshots go to test-results/.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { type Browser, type Page, expect, test } from "@playwright/test";
import { FIXTURES, FRESH_BASE, FRESH_GOS_HOME, PRISM, freshCode, python } from "./fresh";
import { contrastMisses } from "./harness";

const shot = (name: string) => test.info().outputPath(`${name}.png`);
const KEY = `sk-ant-api03-${"E2eW1zard".repeat(8)}`; // fake, the shape of a real key
const TOKEN = `MTIzNDU2Nzg5MDEyMzQ1Njc4OQ.GaBcDe.${"y".repeat(38)}`; // fake, a token's shape
const CONFIG = join(FRESH_GOS_HOME, "config", "gos.toml");

let page: Page;

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }: { browser: Browser }) => {
  page = await browser.newPage({ baseURL: FRESH_BASE, viewport: { width: 2560, height: 1440 } });
});

test.afterAll(async () => {
  await page.close();
});

async function fill(id: string, value: string): Promise<void> {
  await page.getByTestId(`setup-field-${id}`).fill(value);
}

test("a fresh install opens the wizard after pairing", async () => {
  await page.goto("/");
  await page.getByTestId("pair-name").fill("Monitor 2");
  await page.getByTestId("pair-start").click();
  await page.getByTestId("pair-code").fill(await freshCode());
  await page.getByTestId("pair-complete").click();
  await expect(page.getByTestId("setup")).toHaveAttribute("data-done", "no");
  await expect(page.getByTestId("setup-step-paths")).toBeVisible();
  await expect(page.getByTestId("shell")).toHaveCount(0);
  await expect(page.getByTestId("setup-close")).toHaveCount(0); // no way around it on a first run
  expect(await contrastMisses(page)).toEqual([]);
  await page.screenshot({ path: shot("setup-paths") });
});

test("paths are checked with doctor's checks, then saved", async () => {
  await fill("prism_root", PRISM);
  await fill("prism_instances", join(PRISM, "instances"));
  await fill("gos_app", join(FIXTURES, "GregtecOS-app"));
  await fill("gos_home", FRESH_GOS_HOME);
  await fill("play", "Nope");
  await page.getByTestId("setup-check").click();
  await expect(page.locator('[data-check="instance.play"]')).toHaveAttribute("data-status", "error");
  await page.getByTestId("setup-save").click();
  await expect(page.getByTestId("setup-error")).toContainText("not saved");
  await fill("play", "GTNH-Play");
  await page.getByTestId("setup-check").click();
  await expect(page.locator('[data-check="instance.play"]')).toHaveAttribute("data-status", "ok");
  await expect(page.locator('[data-check="instance.lab"]')).toHaveAttribute("data-status", "ok");
  await page.getByTestId("setup-save").click();
  await expect(page.getByTestId("setup-step-anthropic")).toBeVisible();
  await expect(page.getByTestId("setup-nav-paths")).toHaveAttribute("data-mark", "done");
});

test("the API key goes to the keyring, never back to the page, and its test call works", async () => {
  const input = page.getByTestId("setup-secret-key");
  await expect(input).toHaveAttribute("type", "password");
  await input.fill("sk-ant-admin01-" + "x".repeat(40));
  await page.getByTestId("setup-store-key").click();
  await expect(page.getByTestId("setup-note-key")).toContainText("Admin API key");
  await input.fill(KEY);
  await page.getByTestId("setup-store-key").click();
  await expect(page.getByTestId("setup-stored-key")).toHaveAttribute("data-stored", "yes");
  await expect(input).toHaveValue("");
  await page.getByTestId("setup-test-key").click();
  await expect(page.getByTestId("setup-note-key")).toContainText("the API answered on claude-haiku-5-5");
  expect(await page.content()).not.toContain(KEY);
  const state = await page.evaluate(async () => (await fetch("/api/setup")).text());
  expect(state).not.toContain(KEY);
  await page.getByTestId("setup-next").click();
});

test("the Discord token too", async () => {
  await expect(page.getByTestId("setup-step-discord")).toBeVisible();
  await page.getByTestId("setup-secret-token").fill(TOKEN);
  await page.getByTestId("setup-store-token").click();
  await expect(page.getByTestId("setup-stored-token")).toHaveAttribute("data-stored", "yes");
  await page.getByTestId("setup-next").click();
});

test("the headset, the talk key and the wake phrase", async () => {
  await expect(page.getByTestId("setup-step-headset")).toBeVisible();
  const output = page.getByTestId("setup-field-output_endpoint");
  await expect(output.locator("option", { hasText: "Arctis Nova Elite Chat" })).toHaveCount(2); // the device, and "Chat"
  await output.selectOption("Headphones (Arctis Nova Elite Chat)");
  await page.getByTestId("setup-save").click();

  await expect(page.getByTestId("setup-step-talk_key")).toBeVisible();
  await fill("talk_key", "F3");
  await page.getByTestId("setup-save").click();
  await expect(page.getByTestId("setup-problem-talk_key")).toContainText("F1-F12");
  await fill("talk_key", "F13");
  await page.getByTestId("setup-save").click();

  await expect(page.getByTestId("setup-step-wake")).toBeVisible();
  await fill("wake_phrase", "hey gregtech");
  await page.getByTestId("setup-save").click();
  await expect(page.getByTestId("setup-problem-wake_phrase")).toContainText("PM08");
  await fill("wake_phrase", "hey archivist");
  await page.getByTestId("setup-save").click();
});

test("doctor's checklist passes and finishing marks setup done", async () => {
  await expect(page.getByTestId("setup-step-checklist")).toBeVisible();
  const rows = page.getByTestId("setup-checks");
  await expect(rows.locator('[data-check="instance.export"]')).toHaveAttribute("data-status", "ok");
  await expect(rows.locator('[data-status="error"]')).toHaveCount(0);
  await expect(rows.locator('[data-check="secret.anthropic"]')).toHaveAttribute("data-status", "ok");
  await expect(rows.locator('[data-check="voice.settings"]')).toHaveAttribute("data-status", "ok");
  expect(await contrastMisses(page)).toEqual([]);
  await page.screenshot({ path: shot("setup-checklist") });
  await page.getByTestId("setup-rerun").click();
  await expect(page.getByTestId("setup-finish")).toBeEnabled();
  await page.getByTestId("setup-finish").click();
  await expect(page.getByTestId("shell")).toBeVisible();
});

test("gos.toml reads back, the keyring holds the secrets, doctor passes", async () => {
  const text = readFileSync(CONFIG, "utf-8");
  expect(text).not.toContain(KEY);
  expect(text).not.toContain(TOKEN);
  const found = await python<{
    prism_instances: string;
    play: string;
    gos_home: string;
    talk_key: string;
    wake_phrase: string;
    output: string;
    completed: string | null;
    errors: string[];
  }>(
    [
      "import json, sys, tomllib",
      "from pathlib import Path",
      "from gregtec import config, doctor",
      "from gregtec_voice import settings",
      "path = Path(sys.argv[1])",
      "cfg = config.load(path)",
      "_, voice = settings.load(path)",
      "data = tomllib.loads(path.read_text('utf-8'))",
      "errors = [r.id for r in doctor.checklist(cfg) if r.status == 'error']",
      "print(json.dumps({'prism_instances': str(cfg.prism_instances), 'play': cfg.play, 'gos_home': str(cfg.gos_home),",
      "  'talk_key': voice.talk_key.label, 'wake_phrase': data['voice']['wake_phrase'],",
      "  'output': data['voice']['output_endpoint'], 'completed': data.get('setup', {}).get('completed_utc'), 'errors': errors}))",
    ].join("\n"),
    CONFIG,
  );
  expect(found).toMatchObject({
    prism_instances: join(PRISM, "instances"),
    play: "GTNH-Play",
    gos_home: FRESH_GOS_HOME,
    talk_key: "F13",
    wake_phrase: "hey archivist",
    output: "Headphones (Arctis Nova Elite Chat)",
    errors: [],
  });
  expect(found.completed).toBeTruthy();
  const ring = JSON.parse(readFileSync(join(FIXTURES, "keyring.json"), "utf-8")) as Record<string, string>;
  expect(ring["gregtec-os/anthropic|anthropic"]).toBe(KEY);
  expect(ring["gregtec-os/discord|discord"]).toBe(TOKEN);
});

test("re-entering the wizard from Settings edits a setting", async () => {
  await page.keyboard.press("9");
  await page.getByTestId("open-setup").click();
  await expect(page.getByTestId("setup")).toHaveAttribute("data-done", "yes");
  await expect(page.getByTestId("setup-field-play")).toHaveValue("GTNH-Play");
  await page.getByTestId("setup-nav-talk_key").click();
  await expect(page.getByTestId("setup-field-talk_key")).toHaveValue("F13");
  await fill("talk_key", "F18");
  await page.getByTestId("setup-save").click();
  await expect(page.getByTestId("setup-step-wake")).toBeVisible();
  expect(readFileSync(CONFIG, "utf-8")).toContain('talk_key = "F18"');
  await page.getByTestId("setup-close").click();
  await expect(page.getByTestId("shell")).toBeVisible();
});

test("the wizard fits the phone", async () => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#/setup");
  await expect(page.getByTestId("setup-step-paths")).toBeVisible();
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width).toBeLessThanOrEqual(390);
  expect(await contrastMisses(page)).toEqual([]);
  await page.screenshot({ path: shot("setup-phone"), fullPage: true });
});
