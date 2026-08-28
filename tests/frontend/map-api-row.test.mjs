import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { buildSandbox, loadCommon, SITE_DIR } from "./helpers.mjs";

// mapApiRow is intentionally module-specific (Open Graph/Meta/Schema each
// interpret PageResult differently, and Sitemap has entirely different
// semantics) - see common.js's header comment. Extract each module's
// mapApiRow by evaluating the file and reading the function back out,
// rather than duplicating the mapping logic here.
function extractMapApiRow(filename) {
  const sandbox = buildSandbox();
  loadCommon(sandbox);
  const code = fs.readFileSync(path.join(SITE_DIR, filename), "utf8");
  vm.runInContext(code, sandbox, { filename });
  return sandbox.mapApiRow;
}

test("app.js mapApiRow: page with no errors/warnings is 'success'", () => {
  const mapApiRow = extractMapApiRow("app.js");
  const row = mapApiRow({
    url: "https://example.ru/",
    requested_url: "https://example.ru/",
    errors: [],
    warnings: [],
    open_graph: { title: "T", image: "https://example.ru/og.png" },
  });
  assert.equal(row.status, "success");
});

test("app.js mapApiRow: a check_failed page becomes 'unavailable' with a friendly message", () => {
  const mapApiRow = extractMapApiRow("app.js");
  const row = mapApiRow({
    check_failed: true,
    requested_url: "https://example.ru/slow",
    check_error: "Страница не ответила за 30 с",
  });
  assert.equal(row.status, "unavailable");
  assert.match(row.issueText, /Возможно, сервер отвечает слишком медленно/);
});

test("meta.js mapApiRow: errors take priority over warnings", () => {
  const mapApiRow = extractMapApiRow("meta.js");
  const row = mapApiRow({
    url: "https://example.ru/",
    meta: { errors: ["Нет <title>"], warnings: ["Пустой meta viewport"] },
  });
  assert.equal(row.status, "error");
  assert.equal(row.message, "Нет <title>");
});

test("schema.js mapApiRow: reports JSON-LD counts in details", () => {
  const mapApiRow = extractMapApiRow("schema.js");
  const row = mapApiRow({
    url: "https://example.ru/",
    schema: { json_ld_count: 2, valid_json_ld_count: 1, invalid_json_ld_count: 1, errors: ["Invalid JSON-LD"] },
  });
  assert.equal(row.status, "error");
  assert.equal(row.details.jsonLdCount, 2);
  assert.equal(row.details.invalidCount, 1);
});

test("sitemap.js mapApiRow: a redirected URL is a 'warning', not an 'error'", () => {
  const mapApiRow = extractMapApiRow("sitemap.js");
  const row = mapApiRow({
    url: "https://example.ru/new",
    requested_url: "https://example.ru/old",
    status_code: 200,
  });
  assert.equal(row.status, "warning");
  assert.match(row.issueText, /example.ru\/old.*example.ru\/new|конечный адрес/);
});

test("sitemap.js mapApiRow: a non-HTML content-type check_failed is a distinct 'error', not 'unavailable' (regression: image-in-sitemap UX)", () => {
  const mapApiRow = extractMapApiRow("sitemap.js");
  const row = mapApiRow({
    check_failed: true,
    requested_url: "https://example.ru/photo.jpg",
    check_error: "Не удалось проверить страницу: Unexpected content type: image/jpeg",
  });
  assert.equal(row.status, "error");
  assert.match(row.message, /Не HTML/);
});

test("sitemap.js mapApiRow: HTTP 4xx on a sitemap URL is an 'error'", () => {
  const mapApiRow = extractMapApiRow("sitemap.js");
  const row = mapApiRow({
    url: "https://example.ru/gone",
    requested_url: "https://example.ru/gone",
    status_code: 404,
  });
  assert.equal(row.status, "error");
});
