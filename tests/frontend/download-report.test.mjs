import test from "node:test";
import assert from "node:assert/strict";
import { buildSandbox, loadCommon, makeEl } from "./helpers.mjs";

// HI.downloadReport fetches the backend-built report.xlsx as a blob and hands
// the browser a download. These tests drive the real function against stubbed
// fetch / URL / document, asserting the request URL, the filename decoding,
// the anchor download, and graceful error handling - with no real network.

function fakeResponse({ ok = true, body = "xlsx-bytes", disposition = null, detail = null } = {}) {
  return {
    ok,
    status: ok ? 200 : 409,
    headers: { get: name => (name.toLowerCase() === "content-disposition" ? disposition : null) },
    async blob() { return { body }; },
    async json() { return detail ? { detail } : {}; },
  };
}

// A URL that still constructs real URLs (common.js uses `new URL`) but also
// carries the object-URL statics downloadReport relies on, with call tracking.
function trackingUrl(calls) {
  class FakeURL extends URL {
    static createObjectURL(blob) { calls.created.push(blob); return "blob:fake"; }
    static revokeObjectURL(u) { calls.revoked.push(u); }
  }
  return FakeURL;
}

function setup({ response, detail } = {}) {
  const calls = { fetchUrls: [], created: [], revoked: [], alerts: [] };
  const anchor = makeEl("a");
  anchor.click = () => { calls.clicked = true; };

  const documentImpl = {
    createElement: tag => (tag === "a" ? anchor : makeEl(tag)),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
    body: makeEl("body"),
  };

  const fetchImpl = async url => { calls.fetchUrls.push(url); return response || fakeResponse(); };

  const sandbox = buildSandbox({ fetchImpl, documentImpl });
  sandbox.URL = trackingUrl(calls);
  sandbox.alert = msg => calls.alerts.push(msg);
  const HI = loadCommon(sandbox);
  return { HI, calls, anchor };
}

test("filenameFromDisposition decodes RFC 5987 and plain forms", () => {
  const { HI } = setup();
  assert.equal(
    HI.filenameFromDisposition("attachment; filename=\"report.xlsx\"; filename*=UTF-8''headinspect-%D1%84.xlsx"),
    "headinspect-ф.xlsx"
  );
  assert.equal(HI.filenameFromDisposition('attachment; filename="plain.xlsx"'), "plain.xlsx");
  assert.equal(HI.filenameFromDisposition(null), null);
});

test("downloadReport fetches the right URL and triggers a named download", async () => {
  const disposition = "attachment; filename=\"report.xlsx\"; filename*=UTF-8''headinspect-example.ru-2026-10-25.xlsx";
  const { HI, calls, anchor } = setup({ response: fakeResponse({ disposition }) });
  const button = makeEl("button");
  button.textContent = "Скачать Excel";

  await HI.downloadReport("job-123", { button });

  assert.equal(calls.fetchUrls.length, 1);
  assert.match(calls.fetchUrls[0], /\/api\/audits\/job-123\/report\.xlsx$/);
  assert.equal(anchor.download, "headinspect-example.ru-2026-10-25.xlsx");
  assert.equal(calls.clicked, true);
  assert.equal(calls.created.length, 1);
  assert.equal(calls.revoked.length, 1);
  // Button is restored.
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, "Скачать Excel");
});

test("downloadReport does nothing without a job id", async () => {
  const { HI, calls } = setup();
  await HI.downloadReport(null);
  assert.equal(calls.fetchUrls.length, 0);
});

test("downloadReport surfaces a backend error message and does not throw", async () => {
  const { HI, calls } = setup({
    response: fakeResponse({ ok: false, detail: "Проверка ещё не завершена." }),
  });
  const button = makeEl("button");
  button.textContent = "Скачать Excel";

  await HI.downloadReport("job-x", { button });

  assert.deepEqual(calls.alerts, ["Проверка ещё не завершена."]);
  assert.equal(calls.clicked, undefined);
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, "Скачать Excel");
});
