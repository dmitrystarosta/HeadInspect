import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { buildSandbox, buildRegistryDocument, loadCommon, loadModuleScript, makeEl, SITE_DIR } from "./helpers.mjs";

// A job waiting for the single execution slot is polled as status "queued".
// home.js must render an explicit "waiting in queue" state (not the normal
// "Проверяем …" progress), and switch to the normal in-progress rendering the
// moment the status leaves "queued".

// Elements updateProgress touches on the normal (non-queued) path.
const IDS = [
  "home-progress-card", "home-found-count", "home-total-count",
  "home-checked-count", "home-progress-percent", "home-progress-bar",
];

function buildHomeSandbox() {
  const registry = {};
  for (const id of IDS) registry[id] = makeEl("div");
  const documentImpl = buildRegistryDocument(registry);
  const sandbox = buildSandbox({ documentImpl });
  loadCommon(sandbox);
  loadModuleScript(sandbox, "home.js");
  return { sandbox, registry };
}

test("updateProgress marks the card as queued for a queued job and skips progress rendering", () => {
  const { sandbox, registry } = buildHomeSandbox();
  assert.equal(typeof sandbox.updateProgress, "function");

  sandbox.updateProgress({
    status: "queued", discovered_urls: 0, checked_urls: 0,
    progress_percent: 0, normalized_url: null,
  });

  assert.ok(registry["home-progress-card"].classList.contains("is-queued"));
  // The normal progress render must NOT have run (no "проверено X" updates):
  // the counters are left untouched, so the card can't look like it's actively
  // auditing.
  assert.equal(registry["home-checked-count"].textContent, "");
  assert.equal(registry["home-total-count"].textContent, "");
});

test("once the slot is acquired the card drops back to the normal in-progress state", () => {
  const { sandbox, registry } = buildHomeSandbox();

  sandbox.updateProgress({ status: "queued", discovered_urls: 0, checked_urls: 0, progress_percent: 0, normalized_url: null });
  assert.ok(registry["home-progress-card"].classList.contains("is-queued"));

  sandbox.updateProgress({
    status: "running", discovered_urls: 89, checked_urls: 10,
    progress_percent: 11, normalized_url: "https://gogodance.ru/",
  });

  assert.equal(registry["home-progress-card"].classList.contains("is-queued"), false);
  // Normal rendering resumed: the real "проверено 10 из 89" numbers are shown.
  assert.equal(registry["home-checked-count"].textContent, "10");
  assert.equal(registry["home-total-count"].textContent, "89");
});

test("index.html carries the queue notice inside the progress card", () => {
  const html = fs.readFileSync(path.join(SITE_DIR, "index.html"), "utf8");
  const progressLine = html.split("\n").find(l => l.includes('id="home-progress-card"'));
  const card = progressLine.slice(progressLine.indexOf('id="home-progress-card"'));
  assert.match(card, /class="progress-queued"/);
  assert.match(card, /Проверка ожидает своей очереди/);
  assert.match(card, /Она начнётся автоматически/);
});

test("CSS hides the normal progress bits (incl. the minutes hint) while queued, and shows the notice", () => {
  const css = fs.readFileSync(path.join(SITE_DIR, "styles.css"), "utf8");
  // The minutes hint belongs to the running audit and must be hidden in queue.
  assert.match(css, /\.progress-card\.is-queued[^{]*\.progress-hint[^{]*\{[^}]*display:\s*none/s);
  // The queue notice is hidden by default and shown only in the queued state.
  assert.match(css, /\.progress-queued\s*\{[^}]*display:\s*none/);
  assert.match(css, /\.progress-card\.is-queued\s+\.progress-queued\s*\{[^}]*display:\s*block/);
});
