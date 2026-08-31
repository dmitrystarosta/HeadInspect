import test from "node:test";
import assert from "node:assert/strict";
import { buildSandbox, buildRegistryDocument, loadCommon, loadModuleScript, makeEl } from "./helpers.mjs";

// Regression test for the domain-cooldown UX bug: previously startAudit()
// tore down the currently-displayed report (workspace/resultsCard hidden,
// currentRows/currentJobId reset) *before* POSTing /api/audits, and the
// submit handler's catch block then force-hid the whole workspace no
// matter what. So if the user was looking at a completed report and
// clicked "Проверить сайт" again while the site was still in its 10-minute
// domain cooldown, the 429 response left them looking at nothing but a
// form error - the report they were just reading had already been wiped.
//
// Fixed by moving all of that teardown to *after* the POST succeeds (see
// the comments in app.js/home.js/meta.js/schema.js/sitemap.js::startAudit
// and their submit handlers). This test drives the real submit handler via
// a (now real, see helpers.mjs) dispatched "submit" event, not by calling
// an internal function directly.
//
// The mock job's single result is deliberately check_failed (an
// "unavailable" row) rather than a normal success/warning/error row: a
// non-empty row list otherwise hits the DOM stub's documented "no real
// HTML parsing" limitation (see tests/frontend/README.md) in renderRows's
// per-row template. An unavailable-only result set still gives genuinely
// non-zero, meaningfully-checkable counts (#count-all, #result-unavailable)
// without ever reaching that code path, since it doesn't match the
// "success" filter renderRows defaults to when there are no problems.

const IDS = [
  "audit-form", "audit-host", "audit-workspace", "checked-count",
  "count-all", "count-errors", "count-ok", "count-warnings",
  "download-btn", "empty-state", "form-error", "found-count",
  "load-more-btn", "progress-bar", "progress-card", "progress-percent",
  "restart-btn", "result-list", "result-total", "result-unavailable",
  "result-unavailable-summary", "results-card", "site-url",
  "tab-all", "tab-errors", "tab-ok", "tab-problems", "tab-unavailable",
  "tab-warnings", "total-count",
];

function buildPageSandbox({ search = "", fetchImpl } = {}) {
  const registry = {};
  for (const id of IDS) registry[id] = makeEl(id === "audit-form" ? "form" : "div");
  registry["site-url"] = makeEl("input");
  registry["site-url"].value = "";

  const documentImpl = buildRegistryDocument(registry);
  const sandbox = buildSandbox({ fetchImpl, documentImpl });
  sandbox.location.search = search;
  loadCommon(sandbox);
  return { sandbox, registry };
}

function jsonResponse(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

const UNAVAILABLE_RESULT = {
  url: "https://example.ru/",
  requested_url: "https://example.ru/",
  status_code: null,
  check_failed: true,
  check_reason: "timeout",
  check_error: "Страница не ответила за 30 с",
};

const COOLDOWN_DETAIL =
  "Этот сайт уже проверялся недавно. Полный аудит одного и того же сайта " +
  "можно запускать не чаще, чем раз в 10 мин. Следующую проверку этого " +
  "сайта можно запустить через 9 мин 58 сек.";

async function settle(ticks = 2) {
  for (let i = 0; i < ticks; i += 1) {
    await new Promise(resolve => setTimeout(resolve, 20));
  }
}

test("resubmitting a site still in domain cooldown (429) keeps the already-displayed report on screen", async () => {
  const calls = { status: 0, results: 0, create: 0 };
  const fetchImpl = async (url, options = {}) => {
    if (url.endsWith("/results")) {
      calls.results += 1;
      return jsonResponse({ results: [UNAVAILABLE_RESULT] });
    }
    if (url.includes("/api/audits/")) {
      calls.status += 1;
      return jsonResponse({
        status: "completed",
        discovered_urls: 1,
        checked_urls: 1,
        progress_percent: 100,
        normalized_url: "https://example.ru/",
      });
    }
    if (url.endsWith("/api/audits") && options.method === "POST") {
      calls.create += 1;
      // The domain-cooldown rejection, same shape as the real backend 429
      // (see jobs.py::JobManager.create) - a plain ApiError with `detail`.
      return jsonResponse({ detail: COOLDOWN_DETAIL }, 429);
    }
    throw new Error(`unexpected fetch: ${url}`);
  };

  const { sandbox, registry } = buildPageSandbox({
    search: "?job=job-1&url=https%3A%2F%2Fexample.ru",
    fetchImpl,
  });

  assert.doesNotThrow(() => loadModuleScript(sandbox, "app.js"));
  await settle(); // let the initial ?job= load (status + results) finish

  // Sanity: the initial report actually loaded before we try to break it.
  assert.equal(registry["results-card"].hidden, false);
  assert.equal(registry["progress-card"].hidden, true);
  const snapshot = {
    resultsHidden: registry["results-card"].hidden,
    countAll: registry["count-all"].textContent,
    resultUnavailable: registry["result-unavailable"].textContent,
    resultTotal: registry["result-total"].textContent,
  };
  assert.equal(snapshot.countAll, "1");
  assert.equal(snapshot.resultUnavailable, "1");
  assert.equal(calls.status, 1);
  assert.equal(calls.results, 1);

  // Now the user clicks "Проверить сайт" again for the same site, while
  // it's still within its 10-minute domain cooldown.
  registry["form-error"].hidden = true; // reset, like a fresh page would have it
  registry["audit-form"].requestSubmit();
  await settle();

  // The rejected create() call happened...
  assert.equal(calls.create, 1);
  // ...and the form shows the existing cooldown message near it...
  assert.equal(registry["form-error"].hidden, false);
  assert.equal(registry["form-error"].textContent, COOLDOWN_DETAIL);
  // ...but nothing about the previously-displayed report was touched:
  assert.equal(registry["results-card"].hidden, snapshot.resultsHidden);
  assert.equal(registry["count-all"].textContent, snapshot.countAll);
  assert.equal(registry["result-unavailable"].textContent, snapshot.resultUnavailable);
  assert.equal(registry["result-total"].textContent, snapshot.resultTotal);
  // The progress view for a "new" audit must never have been shown either.
  assert.equal(registry["progress-card"].hidden, true);
  // No new status/results polling happened for a job that was never created.
  assert.equal(calls.status, 1);
  assert.equal(calls.results, 1);
});

test("resubmitting after a successful create() does replace the previous report", async () => {
  // The flip side, so the fix above can't be trivially satisfied by simply
  // never updating anything: a *successful* resubmit must still behave as
  // before - new job, new (different) results, progress shown then
  // results shown again.
  const calls = { status: 0, results: 0, create: 0 };
  let currentJob = "job-1";
  const fetchImpl = async (url, options = {}) => {
    if (url.endsWith("/results")) {
      calls.results += 1;
      return jsonResponse({ results: currentJob === "job-1" ? [UNAVAILABLE_RESULT] : [] });
    }
    if (url.includes("/api/audits/")) {
      calls.status += 1;
      return jsonResponse({
        status: "completed",
        discovered_urls: currentJob === "job-1" ? 1 : 0,
        checked_urls: currentJob === "job-1" ? 1 : 0,
        progress_percent: 100,
        normalized_url: "https://example.ru/",
      });
    }
    if (url.endsWith("/api/audits") && options.method === "POST") {
      calls.create += 1;
      currentJob = "job-2";
      return jsonResponse({ job_id: "job-2", status: "queued" }, 202);
    }
    throw new Error(`unexpected fetch: ${url}`);
  };

  const { sandbox, registry } = buildPageSandbox({
    search: "?job=job-1&url=https%3A%2F%2Fexample.ru",
    fetchImpl,
  });

  assert.doesNotThrow(() => loadModuleScript(sandbox, "app.js"));
  await settle();
  assert.equal(registry["count-all"].textContent, "1");

  registry["audit-form"].requestSubmit();
  await settle();

  assert.equal(calls.create, 1);
  assert.equal(registry["results-card"].hidden, false);
  assert.equal(registry["count-all"].textContent, "0"); // job-2's (empty) results, not job-1's
});
