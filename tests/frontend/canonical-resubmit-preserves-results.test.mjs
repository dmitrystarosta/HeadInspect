import test from "node:test";
import assert from "node:assert/strict";
import { buildSandbox, buildRegistryDocument, loadCommon, loadModuleScript, makeEl } from "./helpers.mjs";

// Same domain-cooldown UX guarantee as resubmit-preserves-results.test.mjs,
// but for the new canonical.js: a 429 on resubmit must NOT wipe the report
// that is already on screen. Also exercises canonical.js's own ?job= restore
// path (openExistingAudit) end-to-end against a mocked completed job.

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

// A canonical unavailable row: keeps counts non-zero without hitting the
// DOM stub's no-real-HTML-parsing limit in renderRows (same rationale as the
// app.js sibling test).
const UNAVAILABLE_RESULT = {
  url: "https://example.ru/",
  requested_url: "https://example.ru/",
  status_code: null,
  check_failed: true,
  check_reason: "timeout",
  check_error: "Страница не ответила за 30 с",
  canonical: { present: false, errors: [], warnings: [], notes: [] },
};

const COOLDOWN_DETAIL = "Этот сайт уже проверялся недавно.";

async function settle(ticks = 2) {
  for (let i = 0; i < ticks; i += 1) {
    await new Promise(resolve => setTimeout(resolve, 20));
  }
}

test("canonical.js: resubmitting during domain cooldown (429) keeps the report on screen", async () => {
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
      return jsonResponse({ detail: COOLDOWN_DETAIL }, 429);
    }
    throw new Error(`unexpected fetch: ${url}`);
  };

  const { sandbox, registry } = buildPageSandbox({
    search: "?job=job-1&url=https%3A%2F%2Fexample.ru",
    fetchImpl,
  });

  assert.doesNotThrow(() => loadModuleScript(sandbox, "canonical.js"));
  await settle();

  assert.equal(registry["results-card"].hidden, false);
  assert.equal(registry["count-all"].textContent, "1");
  assert.equal(calls.status, 1);
  assert.equal(calls.results, 1);

  const snapshot = {
    resultsHidden: registry["results-card"].hidden,
    countAll: registry["count-all"].textContent,
    resultTotal: registry["result-total"].textContent,
  };

  registry["form-error"].hidden = true;
  registry["audit-form"].requestSubmit();
  await settle();

  assert.equal(calls.create, 1);
  assert.equal(registry["form-error"].hidden, false);
  assert.equal(registry["form-error"].textContent, COOLDOWN_DETAIL);
  // The previously-displayed report is untouched:
  assert.equal(registry["results-card"].hidden, snapshot.resultsHidden);
  assert.equal(registry["count-all"].textContent, snapshot.countAll);
  assert.equal(registry["result-total"].textContent, snapshot.resultTotal);
});
