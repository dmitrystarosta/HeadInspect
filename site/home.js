const API_BASE = "https://api.headinspect.ru";
const POLL_INTERVAL_MS = 900;

const $ = (selector, ctx = document) => ctx.querySelector(selector);
const $$ = (selector, ctx = document) => [...ctx.querySelectorAll(selector)];

const form = $("#home-audit-form");
const input = $("#home-site-url");
const formError = $("#home-form-error");
const workspace = $("#home-audit-workspace");
const progressCard = $("#home-progress-card");
const resultsCard = $("#home-results-card");
const submitBtn = form ? $('button[type="submit"]', form) : null;

let pollAbortController = null;

function setHomeAuditUrl(jobId, urlValue) {
  const params = new URLSearchParams();
  if (jobId) params.set("job", jobId);
  if (urlValue) params.set("url", urlValue);
  const query = params.toString();
  window.history.replaceState({}, "", query ? `/?${query}` : "/");

  const headerBrand = $(".site-header .brand");
  if (headerBrand) headerBrand.href = query ? `/?${query}` : "/";
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

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function setSubmitting(value) {
  if (!submitBtn) return;
  submitBtn.disabled = value;
  submitBtn.textContent = value ? "Запускаем…" : "Проверить сайт";
}

function showError(message) {
  formError.textContent = message;
  formError.hidden = false;
}

function setProgress(percent) {
  const safe = Math.max(0, Math.min(100, Number(percent) || 0));
  $("#home-progress-percent").textContent = `${safe}%`;
  $("#home-progress-bar").style.width = `${safe}%`;
}

function setStep(name, state = "done") {
  const el = $(`.step[data-home-step="${name}"]`);
  if (!el) return;
  el.classList.remove("done", "active");
  if (state) el.classList.add(state);
  const icon = $("span", el);
  if (icon) icon.textContent = state === "done" ? "✓" : state === "active" ? "◉" : "○";
}

function resetSteps() {
  $$(".step[data-home-step]").forEach(el => {
    el.classList.remove("done", "active");
    const icon = $("span", el);
    if (icon) icon.textContent = "○";
  });
}

async function apiFetch(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {})
    }
  });

  if (response.status === 429) {
    throw new Error("Слишком много запусков подряд. Подождите немного и попробуйте снова.");
  }

  let data = null;
  try { data = await response.json(); } catch {}

  if (!response.ok) {
    throw new Error(data?.detail || `Ошибка API (${response.status})`);
  }
  return data;
}

function updateProgress(status) {
  const discovered = status.discovered_urls || 0;
  const checked = status.checked_urls || 0;

  $("#home-found-count").textContent = discovered;
  $("#home-total-count").textContent = discovered;
  $("#home-checked-count").textContent = checked;
  setProgress(status.progress_percent || 0);

  if (status.normalized_url) setStep("site", "done");
  else setStep("site", "active");

  if (status.status === "discovering") {
    setStep("robots", "active");
    return;
  }

  if (status.robots_found !== null && status.robots_found !== undefined) setStep("robots", "done");
  if (status.sitemap_urls?.length || status.status === "running" || status.status === "completed") setStep("sitemap", "done");
  if (discovered > 0) setStep("urls", "done");
  if (status.status === "running") setStep("scan", "active");
  if (status.status === "completed") {
    setStep("scan", "done");
    setProgress(100);
  }
}

function moduleTotals(pages, moduleName) {
  if (moduleName === "meta") {
    return pages.reduce((totals, page) => {
      const meta = page.meta || {};
      totals.errors += Array.isArray(meta.errors) ? meta.errors.length : 0;
      totals.warnings += Array.isArray(meta.warnings) ? meta.warnings.length : 0;
      return totals;
    }, { errors: 0, warnings: 0 });
  }

  return pages.reduce((totals, page) => {
    totals.errors += Array.isArray(page.errors) ? page.errors.length : 0;
    totals.warnings += Array.isArray(page.warnings) ? page.warnings.length : 0;
    return totals;
  }, { errors: 0, warnings: 0 });
}

function renderAuditModule(moduleName, pages, status, jobId) {
  const module = $(`.audit-module[data-module="${moduleName}"]`);
  if (!module) return;

  const { errors, warnings } = moduleTotals(pages, moduleName);
  const dot = $(".module-status", module);
  const counts = $(".module-counts", module);
  const bodyText = $(".audit-module-body p", module);
  const link = $(".audit-module-body a", module);

  dot?.classList.remove("error", "warning", "success", "soon");
  dot?.classList.add(errors ? "error" : warnings ? "warning" : "success");

  if (counts) {
    if (errors || warnings) {
      counts.innerHTML = `<strong>${errors} ${plural(errors, "ошибка", "ошибки", "ошибок")}</strong><small>${warnings} ${plural(warnings, "предупреждение", "предупреждения", "предупреждений")}</small>`;
    } else {
      counts.innerHTML = `<strong class="module-ok">Ошибок нет</strong><small>Предупреждений нет</small>`;
    }
  }

  const checkedText = moduleName === "meta"
    ? `Meta-теги проверены на ${pages.length} страницах.`
    : `Open Graph проверен на ${pages.length} страницах.`;

  if (bodyText) {
    bodyText.textContent = errors || warnings
      ? `${checkedText} Найдено ошибок: ${errors}, предупреждений: ${warnings}.`
      : `${checkedText} Ошибок и предупреждений не найдено.`;
  }

  if (link && status.normalized_url) {
    const basePath = moduleName === "meta" ? "/meta/" : "/open-graph/";
    link.href = `${basePath}?job=${encodeURIComponent(jobId)}&url=${encodeURIComponent(status.normalized_url)}`;
    link.textContent = moduleName === "meta"
      ? "Открыть подробный Meta-аудит"
      : "Открыть подробный Open Graph-аудит";
  }
}

function renderHomeResult(status, results, jobId) {
  const pages = results?.results || [];
  $("#home-result-total").textContent = status.checked_urls ?? pages.length;

  renderAuditModule("open-graph", pages, status, jobId);
  renderAuditModule("meta", pages, status, jobId);

  progressCard.hidden = true;
  resultsCard.hidden = false;
  resultsCard.scrollIntoView({ behavior: "smooth", block: "start" });
}

function plural(n, one, few, many) {
  const mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

async function startAudit(url) {
  if (pollAbortController) pollAbortController.abort();
  pollAbortController = new AbortController();

  workspace.hidden = false;
  resultsCard.hidden = true;
  progressCard.hidden = false;
  resetSteps();
  setProgress(0);
  $("#home-audit-host").textContent = url.hostname;
  $("#home-found-count").textContent = "0";
  $("#home-checked-count").textContent = "0";
  $("#home-total-count").textContent = "0";
  setStep("site", "active");
  workspace.scrollIntoView({ behavior: "smooth", block: "start" });

  const created = await apiFetch("/api/audits", {
    method: "POST",
    body: JSON.stringify({ url: url.href }),
    signal: pollAbortController.signal
  });
  setHomeAuditUrl(created.job_id, url.href);
  setSubmitting(false);

  while (true) {
    if (pollAbortController.signal.aborted) return;
    const status = await apiFetch(`/api/audits/${created.job_id}`, {
      signal: pollAbortController.signal
    });

    updateProgress(status);

    if (status.status === "failed") {
      throw new Error(status.error || "Не удалось выполнить аудит.");
    }

    if (status.status === "completed") {
      const results = await apiFetch(`/api/audits/${created.job_id}/results`, {
        signal: pollAbortController.signal
      });
      renderHomeResult(status, results, created.job_id);
      return;
    }

    await sleep(POLL_INTERVAL_MS);
  }
}

async function openExistingHomeAudit(jobId, fallbackUrl = null) {
  if (pollAbortController) pollAbortController.abort();
  pollAbortController = new AbortController();

  workspace.hidden = false;
  resultsCard.hidden = true;
  progressCard.hidden = false;
  resetSteps();
  setProgress(0);

  if (fallbackUrl) {
    input.value = fallbackUrl;
    try {
      const u = new URL(fallbackUrl);
      $("#home-audit-host").textContent = u.hostname;
    } catch {}
  }

  workspace.scrollIntoView({ behavior: "smooth", block: "start" });

  while (true) {
    if (pollAbortController.signal.aborted) return;

    const status = await apiFetch(`/api/audits/${jobId}`, {
      signal: pollAbortController.signal
    });
    updateProgress(status);

    if (status.normalized_url) {
      input.value = status.normalized_url;
      setHomeAuditUrl(jobId, status.normalized_url);
      try {
        const u = new URL(status.normalized_url);
        $("#home-audit-host").textContent = u.hostname;
      } catch {}
    }

    if (status.status === "failed") {
      throw new Error(status.error || "Не удалось выполнить аудит.");
    }

    if (status.status === "completed") {
      const results = await apiFetch(`/api/audits/${jobId}/results`, {
        signal: pollAbortController.signal
      });
      renderHomeResult(status, results, jobId);
      return;
    }

    await sleep(POLL_INTERVAL_MS);
  }
}

form?.addEventListener("submit", async event => {
  event.preventDefault();
  formError.hidden = true;

  const url = normalizeUrl(input.value);
  if (!url) {
    showError("Введите корректный адрес сайта, например https://example.ru");
    input.focus();
    return;
  }

  setSubmitting(true);
  try {
    await startAudit(url);
  } catch (error) {
    if (error?.name === "AbortError") return;
    workspace.hidden = true;
    showError(error?.message || "Не удалось запустить проверку. Попробуйте позже.");
    window.scrollTo({ top: 0, behavior: "smooth" });
  } finally {
    setSubmitting(false);
  }
});

$("#home-restart-btn")?.addEventListener("click", () => {
  pollAbortController?.abort();
  workspace.hidden = true;
  resultsCard.hidden = true;
  setHomeAuditUrl(null, null);
  input.focus();
  window.scrollTo({ top: 0, behavior: "smooth" });
});

// Open/close each working module independently.
$$('.audit-module.active .audit-module-head').forEach(moduleButton => {
  moduleButton.addEventListener("click", () => {
    const module = moduleButton.closest(".audit-module");
    const body = $(".audit-module-body", module);
    if (!body) return;
    const open = !body.hidden;
    body.hidden = open;
    moduleButton.setAttribute("aria-expanded", String(!open));
    const arrow = $(".module-arrow", moduleButton);
    if (arrow) arrow.textContent = open ? "⌄" : "⌃";
  });
});

// FAQ accordion.
$$(".faq-list details").forEach(detail => {
  detail.addEventListener("toggle", () => {
    if (!detail.open) return;
    $$(".faq-list details").forEach(other => {
      if (other !== detail && other.open) other.open = false;
    });
  });
});

// Mobile navigation.
const menuToggle = $(".menu-toggle");
const nav = $(".main-nav");
if (menuToggle && nav) {
  menuToggle.addEventListener("click", () => {
    const open = nav.classList.toggle("open");
    menuToggle.setAttribute("aria-expanded", String(open));
  });
  $$(".main-nav a").forEach(a => a.addEventListener("click", () => {
    nav.classList.remove("open");
    menuToggle.setAttribute("aria-expanded", "false");
  }));
}

const initialParams = new URLSearchParams(window.location.search);
const initialJob = initialParams.get("job");
const initialUrl = initialParams.get("url");

if (form && input && initialJob) {
  if (initialUrl) input.value = initialUrl;
  window.setTimeout(async () => {
    try {
      await openExistingHomeAudit(initialJob, initialUrl);
    } catch (error) {
      workspace.hidden = true;
          showError(error?.message || "Не удалось открыть результаты общего аудита.");
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }, 0);
} else if (form && input && initialUrl) {
  input.value = initialUrl;
}
