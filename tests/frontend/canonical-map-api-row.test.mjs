import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { buildSandbox, loadCommon, SITE_DIR } from "./helpers.mjs";

// canonical.js reads PageResult.canonical and produces the module's own
// "страница → canonical → результат" row. Extract its mapApiRow by evaluating
// the file in a sandbox, the same technique map-api-row.test.mjs uses.
function mapApiRow() {
  const sandbox = buildSandbox();
  loadCommon(sandbox);
  const code = fs.readFileSync(path.join(SITE_DIR, "canonical.js"), "utf8");
  vm.runInContext(code, sandbox, { filename: "canonical.js" });
  return sandbox.mapApiRow;
}

const page = (canonical, extra = {}) => ({
  url: "https://ex.ru/p",
  requested_url: "https://ex.ru/p",
  canonical,
  ...extra,
});

test("canonical: absent canonical is a warning with 'не указан' verdict", () => {
  const row = mapApiRow()(page({ present: false, errors: [], warnings: ["Canonical не указан"], notes: [] }));
  assert.equal(row.status, "warning");
  assert.equal(row.canonicalDisplay, "—");
  assert.match(row.verdict, /не указан/);
});

test("canonical: self-canonical is success", () => {
  const row = mapApiRow()(page({
    present: true, is_self: true, resolved_url: "https://ex.ru/p",
    errors: [], warnings: [], notes: [],
  }));
  assert.equal(row.status, "success");
  assert.equal(row.canonicalDisplay, "https://ex.ru/p");
  assert.match(row.verdict, /корректен/);
});

test("canonical: cross-domain is success with 'другой домен' verdict", () => {
  const row = mapApiRow()(page({
    present: true, is_self: false, cross_domain: true,
    resolved_url: "https://other.com/x", errors: [], warnings: [], notes: ["Canonical ведёт на другой домен: other.com"],
  }));
  assert.equal(row.status, "success");
  assert.match(row.verdict, /другой домен/);
});

test("canonical: same-site other page is success with 'другую страницу' verdict", () => {
  const row = mapApiRow()(page({
    present: true, is_self: false, same_site: true,
    resolved_url: "https://ex.ru/other", errors: [], warnings: [], notes: [],
  }));
  assert.equal(row.status, "success");
  assert.match(row.verdict, /другую страницу/);
});

test("canonical: errors take priority over warnings", () => {
  const row = mapApiRow()(page({
    present: true, resolved_url: "https://ex.ru/gone",
    errors: ["Canonical ведёт на страницу с ошибкой HTTP 404"],
    warnings: ["Canonical ведёт на URL с редиректом"], notes: [],
  }));
  assert.equal(row.status, "error");
  assert.match(row.message, /HTTP 404/);
  assert.match(row.verdict, /Проблема/);
});

test("canonical: a check_failed page becomes 'unavailable'", () => {
  const row = mapApiRow()({
    check_failed: true,
    check_reason: "timeout",
    requested_url: "https://ex.ru/slow",
    check_error: "Страница не ответила за 30 с",
  });
  assert.equal(row.status, "unavailable");
  assert.equal(row.canonicalDisplay, "—");
});

test("canonical: details carry target/chain/source fields for the expanded view", () => {
  const row = mapApiRow()(page({
    present: true, resolved_url: "https://ex.ru/b", source: "both",
    is_self: false, same_site: true, target_in_audit: true, target_status: 200,
    target_redirected: true, chain: ["https://ex.ru/p", "https://ex.ru/b"],
    errors: [], warnings: ["Canonical ведёт на URL с редиректом (конечный адрес: https://ex.ru/c)"], notes: [],
  }));
  assert.equal(row.details.source, "both");
  assert.equal(row.details.targetInAudit, true);
  assert.equal(row.details.targetStatus, 200);
  assert.equal(row.details.targetRedirected, true);
});
