import test from "node:test";
import assert from "node:assert/strict";
import { buildSandbox, buildRegistryDocument, loadCommon, loadModuleScript, makeEl } from "./helpers.mjs";

// app.js (Open Graph) is used as the representative "list module": its
// shared plumbing (progress/steps, polling, access-blocked, job-expired,
// partial-results) is byte-identical to meta.js/schema.js/sitemap.js
// because it all now comes from common.js - see tests/frontend/README.md.
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

test("opening an existing job that no longer exists shows the job-expired screen, not raw API text (item 7)", async () => {
  const fetchImpl = async (url) => {
    if (url.includes("/api/audits/")) {
      return jsonResponse({ detail: "Audit job not found" }, 404);
    }
    throw new Error(`unexpected fetch: ${url}`);
  };
  const { sandbox, registry } = buildPageSandbox({
    search: "?job=gone-job&url=https%3A%2F%2Fexample.ru",
    fetchImpl,
  });

  assert.doesNotThrow(() => loadModuleScript(sandbox, "app.js"));
  // The initial-job branch runs on a 0ms setTimeout inside app.js.
  await new Promise(resolve => setTimeout(resolve, 20));

  assert.equal(registry["progress-card"].hidden, true);
  assert.equal(registry["results-card"].hidden, false);
  assert.doesNotMatch(registry["results-card"].innerHTML, /Audit job not found/);
  assert.match(registry["results-card"].innerHTML, /больше не сохранены|недоступны/i);
});

test("opening an existing completed_partial job renders results plus the partial banner, no exception (items 8/9)", async () => {
  let call = 0;
  const fetchImpl = async (url) => {
    if (url.endsWith("/results")) {
      // An empty result set is enough to exercise the partial-results
      // wiring end to end without depending on this test's minimal DOM
      // stub being able to parse the `innerHTML` row markup the way a
      // real browser would (see tests/frontend/README.md).
      return jsonResponse({ results: [] });
    }
    if (url.includes("/api/audits/")) {
      call += 1;
      return jsonResponse({
        status: "completed_partial",
        discovered_urls: 10,
        checked_urls: 4,
        progress_percent: 40,
        normalized_url: "https://example.ru/",
        partial_reason: "Сайт начал ограничивать автоматические запросы HeadInspect во время проверки.",
      });
    }
    throw new Error(`unexpected fetch: ${url}`);
  };
  const { sandbox, registry } = buildPageSandbox({
    search: "?job=job-1&url=https%3A%2F%2Fexample.ru",
    fetchImpl,
  });

  assert.doesNotThrow(() => loadModuleScript(sandbox, "app.js"));
  await new Promise(resolve => setTimeout(resolve, 20));

  assert.equal(registry["results-card"].hidden, false);
  // renderPartialNotice prepends a real child element (not an innerHTML
  // string), so check the banner node itself.
  assert.equal(registry["results-card"].children.length, 1);
  assert.match(registry["results-card"].children[0].innerHTML, /Сайт начал ограничивать автоматические запросы/);
  assert.equal(call, 1);
});

test("opening an existing job whose entry page was access-blocked shows the existing 403 screen unchanged", async () => {
  const fetchImpl = async (url) => {
    if (url.includes("/api/audits/")) {
      return jsonResponse({
        status: "completed",
        discovered_urls: 0,
        checked_urls: 0,
        access_blocked_status: 403,
      });
    }
    throw new Error(`unexpected fetch: ${url}`);
  };
  const { sandbox, registry } = buildPageSandbox({
    search: "?job=job-2&url=https%3A%2F%2Fexample.ru",
    fetchImpl,
  });

  assert.doesNotThrow(() => loadModuleScript(sandbox, "app.js"));
  await new Promise(resolve => setTimeout(resolve, 20));

  assert.match(registry["results-card"].innerHTML, /Сайт не удалось проверить/);
  assert.match(registry["results-card"].innerHTML, /HTTP 403/);
});
