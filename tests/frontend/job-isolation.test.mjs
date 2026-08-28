import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import fs from "node:fs";
import path from "node:path";
import { buildSandbox, buildRegistryDocument, loadCommon, makeEl, SITE_DIR } from "./helpers.mjs";

// Real production incident (2026-08-28): after a job for zipkran.ru finished
// completed_partial (mid-audit HTTP 403 block, 383 inflated failed_checks,
// 129 checked_urls), a *new* job for bionicashow.ru still showed zipkran's
// partial-completion banner and a stale "не удалось проверить: 383" text
// fragment, even though bionicashow's own audit completed normally.
//
// Root cause (see CHANGELOG): renderHomeResult trusted the backend's raw
// status.failed_checks counter (which a separate backend race condition
// could inflate) instead of recomputing from the actual unique pages, and
// only conditionally rewrote the summary text when failedChecks was
// truthy - leaving old text behind when a later job's failedChecks was 0.
// Both are fixed; this test renders two jobs in a row into the same reused
// DOM (as the real single-page app does) and asserts job B's render is
// fully self-contained.

function buildHomeIds() {
  const strong = makeEl("strong");
  const p = makeEl("p");
  p.appendChild(strong);

  return {
    "home-audit-form": makeEl("form"),
    "home-site-url": makeEl("input"),
    "home-form-error": makeEl("div"),
    "home-audit-workspace": makeEl("div"),
    "home-progress-card": makeEl("div"),
    "home-results-card": makeEl("div"),
    "home-audit-host": makeEl("span"),
    "home-found-count": makeEl("b"),
    "home-checked-count": makeEl("b"),
    "home-total-count": makeEl("b"),
    "home-progress-percent": makeEl("strong"),
    "home-progress-bar": makeEl("div"),
    "home-restart-btn": makeEl("button"),
    "home-result-total": strong, // strong.parentElement === p, as in real markup
  };
}

function loadHome(sandbox) {
  const code = fs.readFileSync(path.join(SITE_DIR, "home.js"), "utf8");
  vm.runInContext(code, sandbox, { filename: "home.js" });
}

function makePage(urlPrefix, i, { checkFailed = false, status = 200 } = {}) {
  const url = `${urlPrefix}/p${i}`;
  return {
    url,
    requested_url: url,
    status_code: checkFailed ? null : status,
    check_failed: checkFailed,
    check_error: checkFailed ? "Не удалось проверить страницу: Unexpected content type: image/jpeg" : null,
    errors: [],
    warnings: [],
    open_graph: {},
    meta: {},
    schema: {},
  };
}

test("sequential jobs: a normal completed job never shows a previous completed_partial job's banner or stats (production incident regression)", () => {
  const registry = buildHomeIds();
  const documentImpl = buildRegistryDocument(registry);
  const sandbox = buildSandbox({ documentImpl });
  loadCommon(sandbox);
  loadHome(sandbox);

  // --- Job A: zipkran.ru-shaped - completed_partial, real failures. -------
  const pagesA = [
    ...Array.from({ length: 98 }, (_, i) => makePage("https://zipkran.ru", i)),
    ...Array.from({ length: 31 }, (_, i) => makePage("https://zipkran.ru", 100 + i, { checkFailed: true })),
  ];
  const statusA = {
    status: "completed_partial",
    discovered_urls: 129,
    checked_urls: 129,
    failed_checks: 383, // the real (buggy) inflated backend counter from the incident
    partial_reason: "Сайт начал ограничивать автоматические запросы HeadInspect во время проверки (сервер стал отвечать HTTP 403). Показаны результаты 129 страниц.",
    normalized_url: "https://zipkran.ru/",
  };
  sandbox.renderHomeResult(statusA, { results: pagesA }, "job-a-zipkran");

  assert.equal(registry["home-results-card"].children.length, 1, "job A's partial banner should be present");
  assert.match(registry["home-result-total"].parentElement.innerHTML, /98/, "job A must show the correct unique checked count, not the inflated backend counter");
  assert.match(registry["home-result-total"].parentElement.innerHTML, /не удалось проверить: 31/);

  // --- Job B: bionicashow.ru-shaped - completes normally, no failures. ----
  const pagesB = Array.from({ length: 66 }, (_, i) => makePage("https://bionicashow.ru", i));
  const statusB = {
    status: "completed",
    discovered_urls: 66,
    checked_urls: 66,
    failed_checks: 0,
    partial_reason: null,
    normalized_url: "https://bionicashow.ru/",
  };
  sandbox.renderHomeResult(statusB, { results: pagesB }, "job-b-bionicashow");

  // The banner from job A must be gone.
  assert.equal(registry["home-results-card"].children.length, 0, "job B must not show job A's partial banner");

  // The summary text must be job B's own, with NO trace of job A's numbers.
  const summaryHtml = registry["home-result-total"].parentElement.innerHTML;
  assert.match(summaryHtml, />66</, "must show job B's own checked count");
  assert.doesNotMatch(summaryHtml, /383/, "job A's inflated failed count must not survive");
  assert.doesNotMatch(summaryHtml, /не удалось проверить/, "job B has zero failures - no 'failed' suffix should remain from job A");
});

test("home stats: one URL with check_failed is counted once, not once per module (unique-URL semantics)", () => {
  const registry = buildHomeIds();
  const documentImpl = buildRegistryDocument(registry);
  const sandbox = buildSandbox({ documentImpl });
  loadCommon(sandbox);
  loadHome(sandbox);

  // Exactly the plan's own example: 129 discovered, 98 checked, 31 unique
  // check_failed URLs - each PageResult is ONE record covering every
  // module's view of that page (there is no per-module duplication in the
  // data model), so the count must land on 98/31, never 3x/4x that.
  const pages = [
    ...Array.from({ length: 98 }, (_, i) => makePage("https://example.ru", i)),
    ...Array.from({ length: 31 }, (_, i) => makePage("https://example.ru", 200 + i, { checkFailed: true })),
  ];
  const status = {
    status: "completed",
    discovered_urls: 129,
    checked_urls: 129,
    failed_checks: 31,
    partial_reason: null,
    normalized_url: "https://example.ru/",
  };
  sandbox.renderHomeResult(status, { results: pages }, "job-unique");

  const summaryHtml = registry["home-result-total"].parentElement.innerHTML;
  assert.match(summaryHtml, />98</);
  assert.match(summaryHtml, /не удалось проверить: 31/);
  assert.doesNotMatch(summaryHtml, /93|186|279/, "must never be a multiple of 31 from double/triple/quadruple counting");
});
