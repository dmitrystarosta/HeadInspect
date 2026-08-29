import test from "node:test";
import assert from "node:assert/strict";
import { buildSandbox, loadCommon, makeEl } from "./helpers.mjs";

function setup(opts) {
  const sandbox = buildSandbox(opts);
  const HI = loadCommon(sandbox);
  return { sandbox, HI };
}

test("uniqueContentPages: direct page wins over a redirect to the same final URL", () => {
  const { HI } = setup();
  const pages = [
    { url: "https://example.ru/page", requested_url: "https://example.ru/old-page" }, // via redirect
    { url: "https://example.ru/page", requested_url: "https://example.ru/page" }, // direct
  ];
  const result = HI.uniqueContentPages(pages);
  assert.equal(result.length, 1);
  assert.equal(result[0].requested_url, "https://example.ru/page");
});

test("uniqueContentPages: keeps first redirect record if no direct record exists", () => {
  const { HI } = setup();
  const pages = [
    { url: "https://example.ru/page", requested_url: "https://example.ru/old-1" },
    { url: "https://example.ru/page", requested_url: "https://example.ru/old-2" },
  ];
  const result = HI.uniqueContentPages(pages);
  assert.equal(result.length, 1);
  assert.equal(result[0].requested_url, "https://example.ru/old-1");
});

test("uniqueContentPages: check_failed pages are never merged by url", () => {
  const { HI } = setup();
  const pages = [
    { check_failed: true, requested_url: "https://example.ru/a" },
    { check_failed: true, requested_url: "https://example.ru/b" },
  ];
  const result = HI.uniqueContentPages(pages);
  assert.equal(result.length, 2);
});

test("uniqueContentPages: pages without a final url are kept separately", () => {
  const { HI } = setup();
  const pages = [
    { url: null, requested_url: "https://example.ru/a" },
    { url: "https://example.ru/b", requested_url: "https://example.ru/b" },
  ];
  const result = HI.uniqueContentPages(pages);
  assert.equal(result.length, 2);
});

test("sortRowsForDisplay: home page always first, then by severity, then path", () => {
  const { HI } = setup();
  const rows = [
    { path: "/z", status: "success" },
    { path: "/a", status: "error" },
    { path: "/", status: "success" },
    { path: "/b", status: "warning" },
  ];
  const sorted = HI.sortRowsForDisplay(rows);
  assert.equal(sorted.map(r => r.path).join(","), ["/", "/a", "/b", "/z"].join(","));
});

test("escapeHtml escapes dangerous characters and keeps an em dash placeholder for empty values", () => {
  const { HI } = setup();
  assert.equal(HI.escapeHtml('<img src=x onerror="alert(1)">'), "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
  assert.equal(HI.escapeHtml(null), "—");
  assert.equal(HI.escapeHtml(undefined), "—");
});

test("pluralRu picks the correct Russian plural form", () => {
  const { HI } = setup();
  assert.equal(HI.pluralRu(1, "ошибка", "ошибки", "ошибок"), "ошибка");
  assert.equal(HI.pluralRu(2, "ошибка", "ошибки", "ошибок"), "ошибки");
  assert.equal(HI.pluralRu(5, "ошибка", "ошибки", "ошибок"), "ошибок");
  assert.equal(HI.pluralRu(11, "ошибка", "ошибки", "ошибок"), "ошибок");
  assert.equal(HI.pluralRu(21, "ошибка", "ошибки", "ошибок"), "ошибка");
});

test("apiFetch throws ApiError with the server-provided detail on a non-2xx response", async () => {
  const fetchImpl = async () => ({
    ok: false,
    status: 429,
    json: async () => ({ detail: "Вы уже запустили 3 проверки..." }),
  });
  const { HI } = setup({ fetchImpl });
  await assert.rejects(
    () => HI.apiFetch("/api/audits", { method: "POST" }),
    err => err instanceof HI.ApiError && err.status === 429 && err.message === "Вы уже запустили 3 проверки..."
  );
});

test("apiFetch classifies a 404 with code 'not_found', and isJobNotFound recognizes it", async () => {
  const fetchImpl = async () => ({
    ok: false,
    status: 404,
    json: async () => ({ detail: "Audit job not found" }),
  });
  const { HI } = setup({ fetchImpl });
  try {
    await HI.apiFetch("/api/audits/does-not-exist");
    assert.fail("expected apiFetch to throw");
  } catch (error) {
    assert.ok(HI.isJobNotFound(error));
  }
});

test("apiFetch wraps a network failure (not an AbortError) into a friendly ApiError", async () => {
  const fetchImpl = async () => { throw new Error("getaddrinfo ENOTFOUND"); };
  const { HI } = setup({ fetchImpl });
  await assert.rejects(
    () => HI.apiFetch("/api/audits/x"),
    err => err instanceof HI.ApiError && err.code === "network"
  );
});

test("describeError: an ApiError message is shown to the user as-is (item 4)", () => {
  const { HI } = setup();
  const error = new HI.ApiError("Сервер сайта вернул HTTP 403.", { status: 403 });
  assert.equal(HI.describeError(error), "Сервер сайта вернул HTTP 403.");
});

test("describeError: an unexpected JS error never leaks its raw message to the user (item 4)", () => {
  const { HI } = setup();
  let bug;
  try {
    // Simulate the exact class of bug from the incident report.
    null.textContent = "x";
  } catch (err) {
    bug = err;
  }
  assert.ok(bug instanceof TypeError);
  const message = HI.describeError(bug);
  assert.equal(message, "Не удалось отобразить результаты проверки. Попробуйте обновить страницу.");
  assert.doesNotMatch(message, /TypeError|null|undefined/);
});

test("describeError returns null for an AbortError (caller should silently stop)", () => {
  const { HI } = setup();
  const abort = new Error("aborted");
  abort.name = "AbortError";
  assert.equal(HI.describeError(abort), null);
});

test("pollJobUntilDone resolves once the job reaches 'completed'", async () => {
  const responses = [
    { status: "queued", discovered_urls: 0, checked_urls: 0 },
    { status: "running", discovered_urls: 10, checked_urls: 4 },
    { status: "completed", discovered_urls: 10, checked_urls: 10 },
  ];
  let call = 0;
  const fetchImpl = async () => ({ ok: true, status: 200, json: async () => responses[Math.min(call++, responses.length - 1)] });
  const { HI } = setup({ fetchImpl });
  const seen = [];
  const final = await HI.pollJobUntilDone("job-1", { onStatus: s => seen.push(s.status) });
  assert.equal(final.status, "completed");
  assert.equal(seen.join(","), ["queued", "running", "completed"].join(","));
});

test("pollJobUntilDone resolves on 'completed_partial' too (items 8/9)", async () => {
  const fetchImpl = async () => ({
    ok: true,
    status: 200,
    json: async () => ({ status: "completed_partial", checked_urls: 4, discovered_urls: 10, partial_reason: "test" }),
  });
  const { HI } = setup({ fetchImpl });
  const final = await HI.pollJobUntilDone("job-1", {});
  assert.equal(final.status, "completed_partial");
});

test("pollJobUntilDone throws when the job status is 'failed'", async () => {
  const fetchImpl = async () => ({
    ok: true,
    status: 200,
    json: async () => ({ status: "failed", error: "Сработал общий таймаут" }),
  });
  const { HI } = setup({ fetchImpl });
  await assert.rejects(
    () => HI.pollJobUntilDone("job-1", {}),
    err => err instanceof HI.ApiError && err.message === "Сработал общий таймаут"
  );
});

test("pollJobUntilDone propagates a 404 as an ApiError recognizable via isJobNotFound (item 7)", async () => {
  const fetchImpl = async () => ({
    ok: false,
    status: 404,
    json: async () => ({ detail: "Audit job not found" }),
  });
  const { HI } = setup({ fetchImpl });
  try {
    await HI.pollJobUntilDone("gone-job", {});
    assert.fail("expected pollJobUntilDone to throw");
  } catch (error) {
    assert.ok(HI.isJobNotFound(error));
  }
});

test("renderAccessBlocked returns false and does nothing for a non-block status", () => {
  const { HI } = setup();
  const progressCard = makeEl("div");
  const resultsCard = makeEl("div");
  const handled = HI.renderAccessBlocked({ progressCard, resultsCard }, { access_blocked_status: null });
  assert.equal(handled, false);
  assert.equal(resultsCard.hidden, false); // untouched
});

test("renderAccessBlocked renders the existing '403 blocked' screen and hides progress", () => {
  const { HI } = setup();
  const progressCard = makeEl("div");
  const resultsCard = makeEl("div");
  const handled = HI.renderAccessBlocked({ progressCard, resultsCard }, { access_blocked_status: 403 });
  assert.equal(handled, true);
  assert.equal(progressCard.hidden, true);
  assert.equal(resultsCard.hidden, false);
  assert.match(resultsCard.innerHTML, /Сайт не удалось проверить/);
  assert.match(resultsCard.innerHTML, /HTTP 403/);
  assert.match(resultsCard.innerHTML, /Проверка остановлена/);
});

test("renderJobExpired shows a normal-language message, never the raw API text (item 7)", () => {
  const { HI } = setup();
  const progressCard = makeEl("div");
  const resultsCard = makeEl("div");
  HI.renderJobExpired({ progressCard, resultsCard }, { fallbackUrl: "https://example.ru" });
  assert.equal(progressCard.hidden, true);
  assert.doesNotMatch(resultsCard.innerHTML, /Audit job not found/);
  assert.match(resultsCard.innerHTML, /больше не сохранены|недоступны/i);
});

test("renderPartialNotice only renders for completed_partial and includes the backend's reason (items 8/9)", () => {
  const { HI } = setup();
  const resultsCard = makeEl("div");
  HI.renderPartialNotice(resultsCard, { status: "completed", partial_reason: null });
  assert.equal(resultsCard.children.length, 0);

  HI.renderPartialNotice(resultsCard, { status: "completed_partial", partial_reason: "Сайт начал ограничивать запросы" });
  assert.equal(resultsCard.children.length, 1);
  assert.match(resultsCard.children[0].innerHTML, /Сайт начал ограничивать запросы/);
});

test("renderPartialNotice clears a previous job's banner even when the new job has no notice (regression: stale banner bug)", () => {
  const { HI } = setup();
  const resultsCard = makeEl("div");

  // Job A: completed_partial, banner shown.
  HI.renderPartialNotice(resultsCard, {
    status: "completed_partial",
    partial_reason: "Сайт начал ограничивать автоматические запросы HeadInspect (zipkran.ru).",
  });
  assert.equal(resultsCard.children.length, 1);

  // Job B: renders into the SAME resultsCard element (as happens in the
  // real app - it is never recreated between jobs) and completed normally.
  HI.renderPartialNotice(resultsCard, { status: "completed", partial_reason: null });

  assert.equal(resultsCard.children.length, 0, "job B's render must not leave job A's banner behind");
});

test("renderPartialNotice replaces (not accumulates) the banner across two completed_partial jobs in a row", () => {
  const { HI } = setup();
  const resultsCard = makeEl("div");

  HI.renderPartialNotice(resultsCard, { status: "completed_partial", partial_reason: "Причина job A" });
  HI.renderPartialNotice(resultsCard, { status: "completed_partial", partial_reason: "Причина job B" });

  assert.equal(resultsCard.children.length, 1, "must not accumulate multiple banners");
  assert.match(resultsCard.children[0].innerHTML, /Причина job B/);
  assert.doesNotMatch(resultsCard.children[0].innerHTML, /Причина job A/);
});

test("describeCheckReason: timeout extracts the duration from check_error, not a hard-coded number", () => {
  const { HI } = setup();
  assert.equal(
    HI.describeCheckReason({ check_reason: "timeout", check_error: "Страница не ответила за 30 с" }),
    "Не ответила за 30 с"
  );
  // Robust to a different configured PAGE_TIMEOUT value, not just "30".
  assert.equal(
    HI.describeCheckReason({ check_reason: "timeout", check_error: "Страница не ответила за 45 с" }),
    "Не ответила за 45 с"
  );
});

test("describeCheckReason: access_blocked prefers the page's own title, falls back to the HTTP code", () => {
  const { HI } = setup();
  assert.equal(
    HI.describeCheckReason({ check_reason: "access_blocked", title: "Verification required", status_code: 403 }),
    "Verification required"
  );
  assert.equal(
    HI.describeCheckReason({ check_reason: "access_blocked", title: null, status_code: 429 }),
    "HTTP 429"
  );
});

test("describeCheckReason: network and content_type get distinct, short labels", () => {
  const { HI } = setup();
  assert.equal(HI.describeCheckReason({ check_reason: "network" }), "Нет соединения");
  assert.equal(HI.describeCheckReason({ check_reason: "content_type" }), "Не HTML");
});

test("describeCheckReason: unknown/missing reason falls back to the generic label (regression safety)", () => {
  const { HI } = setup();
  assert.equal(HI.describeCheckReason({}), "Не удалось проверить");
  assert.equal(HI.describeCheckReason({ check_reason: "something_new_and_unhandled" }), "Не удалось проверить");
});
