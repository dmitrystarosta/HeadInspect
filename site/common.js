// Shared infrastructure for HeadInspect's audit pages: home, Open Graph,
// Meta, Schema and Sitemap. This file intentionally holds only generic
// plumbing - API access, job/url context, polling, progress/step display,
// access-blocked/partial/job-expired rendering, and small utilities that
// used to be copy-pasted near-verbatim into five separate files.
//
// Row mapping and issue-detail rendering stay in each page's own script:
// they genuinely differ per module, and Sitemap in particular has different
// URL semantics (it shows every sitemap entry including redirects) than the
// content modules (Open Graph / Meta / Schema show each final page once via
// uniqueContentPages below). Do not "fix" that difference - it's correct.
//
// Loaded as a plain <script> (no bundler/module system in this project), so
// everything here is exposed on the global `HI` object.

(function (global) {
  "use strict";

  const API_BASE = "https://api.headinspect.ru";
  const POLL_INTERVAL_MS = 900;

  function $(selector, ctx = document) {
    return ctx.querySelector(selector);
  }

  function $$(selector, ctx = document) {
    return [...ctx.querySelectorAll(selector)];
  }

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  function normalizeUrl(value) {
    let v = value.trim();
    if (!v) return null;
    if (!/^https?:\/\//i.test(v)) v = "https://" + v;
    try {
      const url = new URL(v);
      if (!url.hostname.includes(".")) return null;
      return url;
    } catch {
      return null;
    }
  }

  function escapeHtml(value) {
    return String(value ?? "—").replace(/[&<>"']/g, ch => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
    }[ch]));
  }

  function escapeAttr(value) {
    return escapeHtml(value);
  }

  function pluralRu(n, one, few, many) {
    const mod10 = n % 10;
    const mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
    return many;
  }

  function getPath(pageUrl) {
    try {
      const u = new URL(pageUrl);
      return `${u.pathname}${u.search}` || "/";
    } catch {
      return pageUrl;
    }
  }

  // --- Error model (item 4) ----------------------------------------------
  // A known, "expected" problem: bad input, rate limiting, access blocked,
  // a timeout reported by the API, a job that no longer exists, a network
  // failure, and so on. `message` is always safe to show to the user as-is.
  class ApiError extends Error {
    constructor(message, { status = null, code = null, retryAfter = null } = {}) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.code = code;
      this.retryAfter = retryAfter;
    }
  }

  // Retry-After from the response header (seconds), falling back to a
  // `retry_after` field in the JSON body. Returns a positive integer or null.
  function parseRetryAfter(response, data) {
    const header = response && response.headers && response.headers.get
      ? response.headers.get("Retry-After")
      : null;
    const raw = header != null ? header : (data && data.retry_after != null ? data.retry_after : null);
    const seconds = raw != null ? parseInt(raw, 10) : NaN;
    return Number.isFinite(seconds) && seconds > 0 ? seconds : null;
  }

  async function apiFetch(path, options = {}) {
    let response;
    try {
      response = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers: {
          "Content-Type": "application/json",
          ...(options.headers || {})
        }
      });
    } catch (error) {
      if (error && error.name === "AbortError") throw error;
      // fetch() rejects ONLY when no readable HTTP response was obtained at all:
      // the browser is offline, DNS/connection failed, or a cross-origin
      // response was refused (e.g. an error response returned without CORS
      // headers, or a blocked preflight). A received HTTP error - 429, 503, any
      // 4xx/5xx - does NOT land here; it resolves and is handled below with the
      // server's own message. So only claim "check your internet" when the
      // browser is actually offline; otherwise the server is reachable but we
      // couldn't read a response (often because it is rate-limiting us), and
      // saying "no connection" would be wrong.
      const offline = typeof navigator !== "undefined" && navigator.onLine === false;
      throw new ApiError(
        offline
          ? "Не удалось связаться с сервером HeadInspect. Проверьте подключение к интернету."
          : "Сервер HeadInspect сейчас не отвечает или временно ограничивает запросы. Подождите немного и повторите попытку.",
        { code: offline ? "offline" : "unreachable" }
      );
    }

    let data = null;
    try { data = await response.json(); } catch { /* no/invalid JSON body */ }

    if (!response.ok) {
      // We DID get an HTTP response - surface it with the backend's own,
      // already-human message; never as a connection failure.
      const detail = data && typeof data.detail === "string" ? data.detail : null;
      const retryAfter = parseRetryAfter(response, data);
      const code =
        response.status === 404 ? "not_found" :
        response.status === 429 ? "rate_limited" : null;
      throw new ApiError(detail || `Ошибка API (${response.status})`, {
        status: response.status, code, retryAfter,
      });
    }

    return data;
  }

  // A safe, user-facing message for *any* caught error: a known ApiError is
  // shown as-is (the backend already produced a normal-language message),
  // while an unexpected JS bug (TypeError and the like) is logged to the
  // console for diagnostics and never shown to the user verbatim.
  function describeError(error, fallback = "Не удалось выполнить проверку. Попробуйте позже.") {
    if (error && error.name === "AbortError") return null;
    if (error instanceof ApiError) {
      return error.message || fallback;
    }
    console.error("HeadInspect: unexpected error", error);
    return "Не удалось отобразить результаты проверки. Попробуйте обновить страницу.";
  }

  function isJobNotFound(error) {
    return error instanceof ApiError && (error.status === 404 || error.code === "not_found");
  }

  // --- Audit URL/job context ----------------------------------------------
  const AUDIT_PATHS = new Set(["/", "/open-graph/", "/meta/", "/canonical/", "/schema/", "/images/", "/sitemap/"]);

  function setAuditContext(jobId, urlValue) {
    if (!jobId) return;
    const params = new URLSearchParams({ job: jobId });
    if (urlValue) params.set("url", urlValue);
    const query = params.toString();
    const currentPath = window.location.pathname;
    window.history.replaceState({}, "", `${currentPath}?${query}`);

    $$(".cross-tool-links a, .main-nav a, .site-header .brand").forEach(link => {
      let target;
      try {
        target = new URL(link.getAttribute("href"), window.location.origin);
      } catch {
        return;
      }
      if (target.origin === window.location.origin && AUDIT_PATHS.has(target.pathname)) {
        link.href = `${target.pathname}?${query}${target.hash}`;
      }
    });
  }

  function clearAuditContext() {
    window.history.replaceState({}, "", window.location.pathname);
  }

  // The home page writes the URL to the address bar even before a job id
  // exists yet, and clears it entirely (not just the query string) when both
  // are empty. Kept as its own function to avoid changing that particular,
  // already-working behaviour while still sharing the same link-rewriting.
  function setHomeAuditUrl(jobId, urlValue) {
    const params = new URLSearchParams();
    if (jobId) params.set("job", jobId);
    if (urlValue) params.set("url", urlValue);
    const query = params.toString();
    window.history.replaceState({}, "", query ? `/?${query}` : "/");

    $$(".main-nav a, .site-header .brand, .cross-tool-links a").forEach(link => {
      let target;
      try {
        target = new URL(link.getAttribute("href"), window.location.origin);
      } catch {
        return;
      }
      if (target.origin !== window.location.origin || !AUDIT_PATHS.has(target.pathname)) return;
      link.href = query ? `${target.pathname}?${query}${target.hash}` : `${target.pathname}${target.hash}`;
    });
  }

  // --- Steps / progress ----------------------------------------------------
  function createStepper(stepAttr) {
    function setStep(name, state = "done") {
      const el = $(`.step[${stepAttr}="${name}"]`);
      if (!el) return;
      el.classList.remove("done", "active");
      if (state) el.classList.add(state);
      const icon = $("span", el);
      if (icon) icon.textContent = state === "done" ? "✓" : state === "active" ? "◉" : "○";
    }
    function resetSteps() {
      $$(`.step[${stepAttr}]`).forEach(el => {
        el.classList.remove("done", "active");
        const icon = $("span", el);
        if (icon) icon.textContent = "○";
      });
    }
    return { setStep, resetSteps };
  }

  // Shared progress/step wiring for the "list" pages (Open Graph, Meta,
  // Schema, Sitemap), whose markup uses #found-count / #progress-bar /
  // data-step consistently. The home page keeps its own small equivalent
  // (different id prefixes and a different module-grid layout), still built
  // on top of the utilities above.
  function createListProgressUi() {
    const stepAttr = "data-step";
    const { setStep, resetSteps: resetStepIconsOnly } = createStepper(stepAttr);

    // This module's markup dynamically overwrites the robots/sitemap step
    // labels (see updateProgressUi below) - unlike the home page's stepper,
    // whose labels are never mutated. Restore those two specific defaults
    // on every reset, so a fresh audit never starts by showing a leftover
    // "robots.txt не найден"/"sitemap не найден" label from a previous job
    // rendered into this same, reused DOM.
    const DEFAULT_STEP_LABELS = {
      robots: " robots.txt найден",
      sitemap: " sitemap найден",
    };

    function resetSteps() {
      resetStepIconsOnly();
      $$(`.step[${stepAttr}]`).forEach(el => {
        const name = el.getAttribute(stepAttr);
        const defaultLabel = DEFAULT_STEP_LABELS[name];
        if (defaultLabel && el.lastChild) el.lastChild.textContent = defaultLabel;
      });
    }

    function setProgress(percent) {
      const safe = Math.max(0, Math.min(100, Number(percent) || 0));
      const percentEl = $("#progress-percent");
      const barEl = $("#progress-bar");
      if (percentEl) percentEl.textContent = `${safe}%`;
      if (barEl) barEl.style.width = `${safe}%`;
    }

    function updateProgressUi(status) {
      const discovered = status.discovered_urls || 0;
      const checked = status.checked_urls || 0;

      const foundEl = $("#found-count");
      const totalEl = $("#total-count");
      const checkedEl = $("#checked-count");
      if (foundEl) foundEl.textContent = discovered;
      if (totalEl) totalEl.textContent = discovered;
      if (checkedEl) checkedEl.textContent = checked;
      setProgress(status.progress_percent || 0);

      if (status.normalized_url) setStep("site", "done");
      else setStep("site", "active");

      if (status.status === "discovering") {
        setStep("robots", "active");
        setStep("sitemap", null);
        setStep("urls", null);
        setStep("scan", null);
        return;
      }

      if (status.robots_found === true) {
        setStep("robots", "done");
        // Explicitly restore the default label: a *previous* job on this
        // same page (same DOM, reused between jobs) may have overwritten
        // it with "robots.txt не найден" below, and that must not survive
        // into a job where robots.txt genuinely was found.
        const robotsStepOk = $(`.step[${stepAttr}="robots"]`);
        if (robotsStepOk && robotsStepOk.lastChild) robotsStepOk.lastChild.textContent = " robots.txt найден";
      } else if (status.robots_found === false) {
        setStep("robots", "done");
        const robotsStep = $(`.step[${stepAttr}="robots"]`);
        if (robotsStep && robotsStep.lastChild) robotsStep.lastChild.textContent = " robots.txt не найден";
      }

      const finished = status.status === "running" || status.status === "completed" || status.status === "completed_partial";
      if (status.sitemap_urls && status.sitemap_urls.length) {
        setStep("sitemap", "done");
        // Same staleness guard as robots.txt above.
        const sitemapStepOk = $(`.step[${stepAttr}="sitemap"]`);
        if (sitemapStepOk && sitemapStepOk.lastChild) sitemapStepOk.lastChild.textContent = " sitemap найден";
      } else if (finished) {
        setStep("sitemap", "done");
        const sitemapStep = $(`.step[${stepAttr}="sitemap"]`);
        if (sitemapStep && sitemapStep.lastChild) sitemapStep.lastChild.textContent = " sitemap не найден — проверена указанная страница";
      }

      if (discovered > 0) setStep("urls", "done");

      if (status.status === "running") setStep("scan", "active");
      if (status.status === "completed") {
        setStep("scan", "done");
        setProgress(100);
      } else if (status.status === "completed_partial") {
        // Leave the real checked/discovered percentage on screen - showing
        // 100% here would misrepresent a genuinely incomplete audit.
        setStep("scan", "done");
      }
    }

    return { setStep, resetSteps, setProgress, updateProgressUi };
  }

  // --- Result-page states shared by every module --------------------------

  function renderAccessBlocked({ progressCard, resultsCard }, status) {
    const code = status.access_blocked_status;
    if (![401, 403, 429].includes(code)) return false;

    const explanations = {
      401: "Сервер требует авторизацию и не разрешил HeadInspect получить страницу.",
      403: "Сервер запретил HeadInspect автоматический доступ. При этом сайт может нормально открываться в обычном браузере.",
      429: "Сервер ограничил частоту автоматических запросов HeadInspect. Попробуйте повторить проверку позже."
    };

    progressCard.hidden = true;
    resultsCard.hidden = false;
    resultsCard.innerHTML = `
      <div class="results-head">
        <div>
          <div class="section-kicker">РЕЗУЛЬТАТ</div>
          <h2>Сайт не удалось проверить</h2>
          <p>Сервер сайта вернул HeadInspect ответ <strong>HTTP ${code}</strong>.</p>
        </div>
      </div>
      <div class="issue-box unavailable">
        <strong>Проверка остановлена</strong>
        <p>${explanations[code]}</p>
        <p>Данные страниц сайта не анализировались, поскольку результаты такой проверки были бы недостоверными.</p>
      </div>`;
    resultsCard.scrollIntoView({ behavior: "smooth", block: "start" });
    return true;
  }

  // Item 7: the job id from ?job=... no longer exists on the backend
  // (restart/deploy, or it simply expired). Never show the raw "Audit job
  // not found" API text - offer a normal-language explanation and, if we
  // still know which URL was being checked, a one-click way to re-run it.
  function renderJobExpired({ progressCard, resultsCard }, { fallbackUrl, onRestart } = {}) {
    progressCard.hidden = true;
    resultsCard.hidden = false;

    const actionHtml = fallbackUrl && typeof onRestart === "function"
      ? `<div class="detail-actions"><button type="button" class="primary-btn" id="hi-job-expired-restart">Запустить проверку заново</button></div>`
      : "";

    resultsCard.innerHTML = `
      <div class="results-head">
        <div>
          <div class="section-kicker">РЕЗУЛЬТАТ</div>
          <h2>Результаты этой проверки больше не сохранены</h2>
        </div>
      </div>
      <div class="issue-box unavailable">
        <strong>Проверка недоступна</strong>
        <p>Результаты этой проверки больше недоступны. Обычно это происходит, если прошло много времени с момента проверки или сервис обновлялся.</p>
        ${actionHtml}
      </div>`;
    resultsCard.scrollIntoView({ behavior: "smooth", block: "start" });

    if (fallbackUrl && typeof onRestart === "function") {
      const btn = $("#hi-job-expired-restart", resultsCard);
      btn?.addEventListener("click", () => onRestart(fallbackUrl));
    }
  }

  // Items 8/9: the audit did not fully complete (AUDIT_TIMEOUT, or the site
  // started blocking automated requests mid-run), but some pages were
  // successfully checked and are still shown. The backend already composed
  // a plain-language explanation in `partial_reason` - just display it.
  function renderPartialNotice(resultsCard, status) {
    // Always clear any banner left over from a *previous* job rendered
    // into this same, reused DOM element first - job state must never
    // bleed across jobs. Without this, a site that completes normally
    // right after a completed_partial job would silently keep showing
    // the previous job's partial-completion banner, since the early
    // return below (for a normal "completed" status) would otherwise
    // never remove it.
    $$(".hi-partial-banner", resultsCard).forEach(el => el.remove());

    if (status.status !== "completed_partial" || !status.partial_reason) return;
    const banner = document.createElement("div");
    banner.className = "issue-box unavailable hi-partial-banner";
    banner.innerHTML = `<strong>Проверка завершена не полностью</strong><p>${escapeHtml(status.partial_reason)}</p>`;
    resultsCard.prepend(banner);
  }

  function isCompletedStatus(status) {
    return status === "completed" || status === "completed_partial";
  }

  // --- "Без ответа" reason label (items 2, 3, 5) ---------------------------
  // A short, human-readable label for a check_failed page, driven by the
  // backend's structured `check_reason` rather than pattern-matching
  // `check_error` text. Shown right on the collapsed row in the "Без
  // ответа" list (not only after clicking into the detail) - the whole
  // point is that a user browsing that list can tell a timeout apart from
  // an access-blocked page apart from a network failure without opening
  // each row individually. The full explanatory sentence (check_error)
  // still appears in the detail panel underneath.
  function describeCheckReason(page) {
    if (page.check_reason === "timeout") {
      // Backend's check_error reads e.g. "Страница не ответила за 30 с" -
      // pull just the duration out of that stable, backend-controlled
      // phrase rather than hard-coding PAGE_TIMEOUT's value here too.
      const match = /не ответила за ([^.]+)/i.exec(page.check_error || "");
      return match ? `Не ответила за ${match[1]}` : "Таймаут";
    }
    if (page.check_reason === "access_blocked") {
      // Prefer the page's own title when the server sent one (e.g.
      // "Verification required") - it's the most concrete, specific label
      // available. Falling back to the HTTP code keeps this useful even
      // when there is no title.
      return page.title || `HTTP ${page.status_code ?? "?"}`;
    }
    if (page.check_reason === "content_type") {
      return "Не HTML";
    }
    if (page.check_reason === "network") {
      return "Нет соединения";
    }
    return "Не удалось проверить";
  }

  // --- Content-module de-duplication ---------------------------------------
  // Open Graph / Meta / Schema show each *final* page once, preferring the
  // page reached directly over one reached only via a redirect. check_failed
  // pages are never merged: their own requested_url is the important thing
  // to show, and merging them by (missing) `url` would be wrong.
  function uniqueContentPages(pages) {
    const byFinalUrl = new Map();
    const withoutFinalUrl = [];

    for (const page of pages || []) {
      if (page.check_failed || !page.url) {
        withoutFinalUrl.push(page);
        continue;
      }

      const key = page.url;
      const existing = byFinalUrl.get(key);
      const isDirect = !page.requested_url || page.requested_url === page.url;
      const existingIsDirect = existing && (!existing.requested_url || existing.requested_url === existing.url);

      if (!existing || (isDirect && !existingIsDirect)) byFinalUrl.set(key, page);
    }

    return [...byFinalUrl.values(), ...withoutFinalUrl];
  }

  function sortRowsForDisplay(rows) {
    const severityRank = { error: 0, warning: 1, success: 2 };

    return [...rows].sort((a, b) => {
      // The home page is always the most important row.
      if (a.path === "/" && b.path !== "/") return -1;
      if (b.path === "/" && a.path !== "/") return 1;

      const severityDiff = (severityRank[a.status] ?? 9) - (severityRank[b.status] ?? 9);
      if (severityDiff !== 0) return severityDiff;

      return a.path.localeCompare(b.path, "ru", { numeric: true, sensitivity: "base" });
    });
  }

  // --- Shared polling loop --------------------------------------------------
  // Polls /api/audits/{jobId} until it reaches a terminal state. Calls
  // onStatus(status) on every poll (for progress/step UI). Resolves with the
  // final status object once it is "completed" or "completed_partial".
  // Throws ApiError("failed", ...) if the job's own status is "failed", or
  // whatever apiFetch threw (including a 404 ApiError if the job no longer
  // exists - callers should check isJobNotFound on catch).
  async function pollJobUntilDone(jobId, { signal, onStatus } = {}) {
    while (true) {
      if (signal && signal.aborted) return null;

      const status = await apiFetch(`/api/audits/${jobId}`, { signal });
      if (typeof onStatus === "function") onStatus(status);

      if (status.status === "failed") {
        throw new ApiError(status.error || "Не удалось выполнить аудит.", { code: "audit_failed" });
      }

      if (isCompletedStatus(status.status)) {
        return status;
      }

      await sleep(POLL_INTERVAL_MS);
    }
  }

  global.HI = {
    API_BASE,
    POLL_INTERVAL_MS,
    $,
    $$,
    sleep,
    normalizeUrl,
    escapeHtml,
    escapeAttr,
    pluralRu,
    getPath,
    ApiError,
    apiFetch,
    describeError,
    isJobNotFound,
    setAuditContext,
    clearAuditContext,
    setHomeAuditUrl,
    createStepper,
    createListProgressUi,
    renderAccessBlocked,
    renderJobExpired,
    renderPartialNotice,
    isCompletedStatus,
    describeCheckReason,
    uniqueContentPages,
    sortRowsForDisplay,
    pollJobUntilDone
  };
})(window);
