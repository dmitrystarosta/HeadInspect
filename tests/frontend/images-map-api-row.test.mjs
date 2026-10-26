import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { buildSandbox, loadCommon, SITE_DIR } from "./helpers.mjs";

// images.js keeps its row mapping and per-image line formatting as module-top
// function declarations (like the other modules' mapApiRow), so they can be
// read back out of the vm sandbox and tested directly.
function loadImages() {
  const sandbox = buildSandbox();
  loadCommon(sandbox);
  const code = fs.readFileSync(path.join(SITE_DIR, "images.js"), "utf8");
  vm.runInContext(code, sandbox, { filename: "images.js" });
  return sandbox;
}

test("images.js mapApiRow: a broken image makes the page an 'error'", () => {
  const { mapApiRow } = loadImages();
  const row = mapApiRow({
    url: "https://example.ru/",
    images: { total: 2, checked_count: 2, items: [],
              errors: ["https://example.ru/x.png: HTTP 404"],
              warnings: ["https://example.ru/y.png: нет атрибута alt"] },
  });
  assert.equal(row.status, "error");
  assert.match(row.message, /HTTP 404/);
});

test("images.js mapApiRow: warnings only => 'warning'", () => {
  const { mapApiRow } = loadImages();
  const row = mapApiRow({
    url: "https://example.ru/",
    images: { total: 1, checked_count: 1, items: [], errors: [], warnings: ["нет атрибута alt"] },
  });
  assert.equal(row.status, "warning");
});

test("images.js mapApiRow: no issues => 'success'", () => {
  const { mapApiRow } = loadImages();
  const row = mapApiRow({
    url: "https://example.ru/",
    images: { total: 3, checked_count: 3, items: [], errors: [], warnings: [] },
  });
  assert.equal(row.status, "success");
  assert.equal(row.message, "Изображения в порядке");
});

test("images.js mapApiRow: no images at all says so", () => {
  const { mapApiRow } = loadImages();
  const row = mapApiRow({ url: "https://example.ru/", images: { total: 0, items: [], errors: [], warnings: [] } });
  assert.equal(row.status, "success");
  assert.equal(row.message, "Изображений не найдено");
});

test("images.js mapApiRow: check_failed page is 'unavailable'", () => {
  const { mapApiRow } = loadImages();
  const row = mapApiRow({
    check_failed: true, check_reason: "network", requested_url: "https://example.ru/down",
  });
  assert.equal(row.status, "unavailable");
  assert.equal(row.message, "Нет соединения");
});

test("images.js imageLine: broken image is red with HTTP code and alt note", () => {
  const { imageLine } = loadImages();
  const line = imageLine({
    src: "https://example.ru/x.png", source: "img", has_alt: false,
    accessible: false, status_code: 404, checked: true, reuse_count: 1,
    errors: ["HTTP 404"], warnings: ["нет атрибута alt"],
  });
  assert.equal(line.state, "bad");
  assert.equal(line.code, "HTTP 404");
  assert.match(line.hint, /нет alt/);
});

test("images.js imageLine: accessible image shows HTTP + size", () => {
  const { imageLine } = loadImages();
  const line = imageLine({
    src: "https://example.ru/ok.webp", source: "img", has_alt: true, alt: "x",
    width_attr: "100", height_attr: "50", format_guess: "WEBP",
    accessible: true, status_code: 200, bytes: 2048, checked: true, reuse_count: 4,
    errors: [], warnings: [],
  });
  assert.equal(line.state, "ok");
  assert.match(line.code, /HTTP 200 · 2 КБ/);
  assert.match(line.hint, /100×50/);
  assert.match(line.hint, /WEBP/);
  assert.match(line.hint, /на 4/);
});

test("images.js imageLine: inconclusive availability shows the note", () => {
  const { imageLine } = loadImages();
  const line = imageLine({
    src: "https://example.ru/q.png", source: "img", has_alt: true, alt: "x",
    width_attr: "1", height_attr: "1",
    accessible: null, checked: true, availability_note: "не удалось определить: HEAD не ответил вовремя",
    reuse_count: 1, errors: [], warnings: [],
  });
  assert.match(line.code, /не удалось определить/);
});

test("images.js imageLine: srcset variant is never flagged for missing alt", () => {
  const { imageLine } = loadImages();
  const line = imageLine({
    src: "https://example.ru/main-2x.png", source: "srcset", has_alt: false,
    accessible: true, status_code: 200, checked: true, reuse_count: 1,
    errors: [], warnings: [],
  });
  assert.doesNotMatch(line.hint, /нет alt/);
  assert.match(line.hint, /вариант srcset/);
});

test("images.js imageLine: a not-checked image is neutral 'не проверено', not an error", () => {
  const { imageLine } = loadImages();
  const line = imageLine({
    src: "https://example.ru/q.png", source: "img", has_alt: true, alt: "x",
    width_attr: "1", height_attr: "1",
    accessible: null, checked: false,
    availability_note: "не проверено: превышен лимит проверок изображений за аудит",
    reuse_count: 1, errors: [], warnings: [],
  });
  assert.equal(line.state, "muted");
  // The reason from availability_note is surfaced, not a bare "не проверено".
  assert.equal(line.code, "не проверено: превышен лимит проверок изображений за аудит");
});

test("home.js moduleTotals sums images errors/warnings", () => {
  const sandbox = buildSandbox();
  loadCommon(sandbox);
  const code = fs.readFileSync(path.join(SITE_DIR, "home.js"), "utf8");
  vm.runInContext(code, sandbox, { filename: "home.js" });
  const totals = sandbox.moduleTotals(
    [
      { images: { errors: ["a", "b"], warnings: ["c"] } },
      { images: { errors: [], warnings: ["d"] } },
    ],
    "images",
  );
  // Compare fields (the object comes from the vm realm, so a structural
  // deepEqual would fail on prototype identity).
  assert.equal(totals.errors, 2);
  assert.equal(totals.warnings, 2);
});
