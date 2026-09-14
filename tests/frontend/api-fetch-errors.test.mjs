import test from "node:test";
import assert from "node:assert/strict";
import { buildSandbox, loadCommon } from "./helpers.mjs";

// Regression: during a load test, per-IP rate-limit 429s coincided with the UI
// showing "Не удалось связаться с сервером HeadInspect. Проверьте подключение к
// интернету." That message must appear ONLY when fetch() truly could not get a
// response (and the browser is offline). A received HTTP response - 429, 503,
// any 4xx/5xx - must be surfaced with the server's own message, never as a
// connection failure.

const OFFLINE_MSG = "Не удалось связаться с сервером HeadInspect. Проверьте подключение к интернету.";

function response({ ok, status, body = {}, headers = {} }) {
  const lower = {};
  for (const k of Object.keys(headers)) lower[k.toLowerCase()] = headers[k];
  return {
    ok,
    status,
    headers: { get: (name) => lower[String(name).toLowerCase()] ?? null },
    async json() { return body; },
  };
}

function makeHI({ fetchImpl, onLine = true } = {}) {
  const sandbox = buildSandbox({ fetchImpl });
  sandbox.navigator = { onLine };
  return loadCommon(sandbox);
}

async function caught(promise) {
  try { await promise; }
  catch (e) { return e; }
  throw new Error("expected the call to reject, but it resolved");
}

test("a readable 429 is surfaced as a rate-limit error, not a connection failure", async () => {
  const detail = "Вы уже запустили 3 проверки за последние 2 минуты. Следующую проверку можно запустить через 42 секунды.";
  const HI = makeHI({
    fetchImpl: async () => response({ ok: false, status: 429, body: { detail }, headers: { "Retry-After": "42" } }),
  });

  const err = await caught(HI.apiFetch("/api/audits", { method: "POST" }));
  assert.ok(err instanceof HI.ApiError);
  assert.equal(err.status, 429);
  assert.equal(err.code, "rate_limited");
  assert.equal(err.message, detail);          // the human rate-limit text
  assert.equal(err.retryAfter, 42);           // parsed from Retry-After
  assert.notEqual(err.message, OFFLINE_MSG);  // never the "no internet" message
  // What the user actually sees (home submit path) is the rate-limit text.
  assert.equal(HI.describeError(err), detail);
});

test("a readable 503 (queue/busy) is surfaced with its own message", async () => {
  const detail = "Сервис сейчас занят: очередь проверок заполнена. Попробуйте через несколько минут.";
  const HI = makeHI({
    fetchImpl: async () => response({ ok: false, status: 503, body: { detail }, headers: { "Retry-After": "60" } }),
  });

  const err = await caught(HI.apiFetch("/api/audits", { method: "POST" }));
  assert.equal(err.status, 503);
  assert.equal(err.message, detail);
  assert.notEqual(err.message, OFFLINE_MSG);
});

test("a genuine offline browser still shows the connection message", async () => {
  const HI = makeHI({
    fetchImpl: async () => { throw new TypeError("Failed to fetch"); },
    onLine: false,
  });

  const err = await caught(HI.apiFetch("/api/audits", { method: "POST" }));
  assert.equal(err.code, "offline");
  assert.equal(err.message, OFFLINE_MSG);
});

test("fetch failing while the browser is online is reported as 'server unreachable/limited', not 'no internet'", async () => {
  // This is the load-test case: the server was up and rate-limiting, but the
  // cross-origin error response was not readable, so fetch() rejected.
  const HI = makeHI({
    fetchImpl: async () => { throw new TypeError("Failed to fetch"); },
    onLine: true,
  });

  const err = await caught(HI.apiFetch("/api/audits", { method: "POST" }));
  assert.equal(err.code, "unreachable");
  assert.notEqual(err.message, OFFLINE_MSG);
  assert.match(err.message, /ограничивает|повторите/i);
});

test("an AbortError is passed through untouched (not turned into a connection error)", async () => {
  const HI = makeHI({
    fetchImpl: async () => { const e = new Error("aborted"); e.name = "AbortError"; throw e; },
  });
  const err = await caught(HI.apiFetch("/api/audits", { method: "POST" }));
  assert.equal(err.name, "AbortError");
});
