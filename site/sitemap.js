const API_BASE = "https://api.headinspect.ru";
const POLL_INTERVAL_MS = 900;

const $ = (selector, ctx = document) => ctx.querySelector(selector);
const $$ = (selector, ctx = document) => [...ctx.querySelectorAll(selector)];

const form = $("#audit-form");
const input = $("#site-url");
const formError = $("#form-error");
const workspace = $("#audit-workspace");
const progressCard = $("#progress-card");
const resultsCard = $("#results-card");
const resultList = $("#result-list");
const loadMoreBtn = $("#load-more-btn");
const emptyState = $("#empty-state");
const submitBtn = form ? $('button[type="submit"]', form) : null;

let activeFilter = "problems";
let currentRows = [];
let visibleLimit = 50;
let currentJobId = null;
let pollAbortController = null;
let currentAuditStatus = null;

function setAuditContext(jobId, urlValue) {
  if (!jobId) return;
  const params = new URLSearchParams({ job: jobId });
  if (urlValue) params.set("url", urlValue);
  const query = params.toString();
  const currentPath = window.location.pathname;
  window.history.replaceState({}, "", `${currentPath}?${query}`);

  const supportedPaths = new Set(["/", "/open-graph/", "/meta/", "/canonical/", "/schema/", "/images/", "/sitemap/"]);
  $$(".cross-tool-links a, .main-nav a, .site-header .brand").forEach(link => {
    const target = new URL(link.getAttribute("href"), window.location.origin);
    if (target.origin === window.location.origin && supportedPaths.has(target.pathname)) {
      link.href = `${target.pathname}?${query}${target.hash}`;
    }
  });
}

function clearAuditContext() {
  window.history.replaceState({}, "", window.location.pathname);
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

function setStep(name, state = "done") {
  const el = $(`.step[data-step="${name}"]`);
  if (!el) return;
  el.classList.remove("done", "active");
  if (state) el.classList.add(state);
  const icon = $("span", el);
  if (icon) icon.textContent = state === "done" ? "✓" : state === "active" ? "◉" : "○";
}

function resetSteps() {
  $$(".step").forEach(el => {
    el.classList.remove("done", "active");
    const icon = $("span", el);
    if (icon) icon.textContent = "○";
  });
}

function setProgress(percent) {
  const safe = Math.max(0, Math.min(100, Number(percent) || 0));
  $("#progress-percent").textContent = `${safe}%`;
  $("#progress-bar").style.width = `${safe}%`;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function showFormError(message) {
  formError.textContent = message;
  formError.hidden = false;
}

function setSubmitting(isSubmitting) {
  if (!submitBtn) return;
  submitBtn.disabled = isSubmitting;
  submitBtn.textContent = isSubmitting ? "Запускаем…" : "Проверить сайт";
}

function getPath(pageUrl) {
  try {
    const u = new URL(pageUrl);
    return `${u.pathname}${u.search}` || "/";
  } catch {
    return pageUrl;
  }
}

function unusedMetaHint(value, type) {
  if (type === "keywords") {
    if (!value || value === "—") {
      return { text: "не указан — рекомендация для Яндекса; Google meta keywords не использует", state: "warn" };
    }
    return { text: "meta keywords указан", state: "ok" };
  }

  if (type === "robots") {
    if (!value || value === "—") {
      return { text: "не указан — это нормально; по умолчанию index, follow", state: "ok" };
    }
    return { text: "директивы meta robots указаны", state: "ok" };
  }

  if (!value || value === "—") return { text: "нет значения", state: "warn" };
  return { text: "указано", state: "ok" };
}

function mapApiRow(page) {
  const originalUrl = page.requested_url || page.url;
  const finalUrl = page.url || originalUrl;
  const pageErrors = Array.isArray(page.errors) ? page.errors : [];
  const statusCode = page.status_code;
  const redirected = originalUrl && finalUrl && originalUrl !== finalUrl;

  let status = "success";
  let message = statusCode ? `HTTP ${statusCode}` : "URL недоступен";
  let issueTitle = "URL доступен";
  let issueText = statusCode ? `Страница отвечает HTTP ${statusCode}.` : "Не удалось получить ответ страницы.";

  if (pageErrors.length || !statusCode || statusCode >= 400) {
    status = "error";
    message = pageErrors[0] || (statusCode ? `HTTP ${statusCode}` : "URL недоступен");
    issueTitle = "URL из sitemap недоступен";
    issueText = pageErrors.join(" · ") || message;
  } else if (redirected || (statusCode >= 300 && statusCode < 400)) {
    status = "warning";
    message = redirected ? "Редирект на другой URL" : `HTTP ${statusCode}`;
    issueTitle = "URL перенаправляется";
    issueText = redirected ? `В sitemap указан ${originalUrl}, конечный адрес — ${finalUrl}.` : message;
  }

  return {
    status,
    path: getPath(originalUrl),
    pageUrl: finalUrl,
    message,
    issueTitle,
    issueText,
    details: {
      originalUrl,
      finalUrl,
      statusCode: statusCode ?? "—",
      redirected
    }
  };
}

function missingSitemapRow() {
  return {
    status: "error",
    path: "/sitemap.xml",
    pageUrl: null,
    message: "Sitemap не найден",
    issueTitle: "Карта сайта не найдена",
    issueText: "HeadInspect не нашёл sitemap через robots.txt и стандартные адреса /sitemap.xml и /sitemap_index.xml.",
    details: { originalUrl: "—", finalUrl: "—", statusCode: "—", redirected: false }
  };
}
function updateProgressUi(status) {
  const discovered = status.discovered_urls || 0;
  const checked = status.checked_urls || 0;

  $("#found-count").textContent = discovered;
  $("#total-count").textContent = discovered;
  $("#checked-count").textContent = checked;
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

  if (status.robots_found === true) setStep("robots", "done");
  else if (status.robots_found === false) {
    setStep("robots", "done");
    const robotsStep = $('.step[data-step="robots"]');
    if (robotsStep) robotsStep.lastChild.textContent = " robots.txt не найден";
  }

  if (status.sitemap_urls && status.sitemap_urls.length) setStep("sitemap", "done");
  else if (status.status === "running" || status.status === "completed") {
    setStep("sitemap", "done");
    const sitemapStep = $('.step[data-step="sitemap"]');
    if (sitemapStep) sitemapStep.lastChild.textContent = " sitemap не найден — проверена указанная страница";
  }

  if (discovered > 0) setStep("urls", "done");

  if (status.status === "running") setStep("scan", "active");
  if (status.status === "completed") {
    setStep("scan", "done");
    setProgress(100);
  }
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
  try {
    data = await response.json();
  } catch {
    // handled below
  }

  if (!response.ok) {
    const detail = data && data.detail ? data.detail : `Ошибка API (${response.status})`;
    throw new Error(detail);
  }

  return data;
}

async function startAudit(url) {
  if (pollAbortController) pollAbortController.abort();
  pollAbortController = new AbortController();

  workspace.hidden = false;
  resultsCard.hidden = true;
  progressCard.hidden = false;
  workspace.scrollIntoView({ behavior: "smooth", block: "start" });

  currentRows = [];
  currentJobId = null;
  resetSteps();
  setProgress(0);
  $("#audit-host").textContent = url.hostname;
  $("#found-count").textContent = "0";
  $("#checked-count").textContent = "0";
  $("#total-count").textContent = "0";
  setStep("site", "active");

  const created = await apiFetch("/api/audits", {
    method: "POST",
    body: JSON.stringify({ url: url.href }),
    signal: pollAbortController.signal
  });

  currentJobId = created.job_id;
  setAuditContext(currentJobId, url.href);
  setSubmitting(false);

  while (true) {
    if (pollAbortController.signal.aborted) return;

    const status = await apiFetch(`/api/audits/${currentJobId}`, {
      signal: pollAbortController.signal
    });
    currentAuditStatus = status;
    updateProgressUi(status);

    if (status.status === "failed") {
      throw new Error(status.error || "Не удалось выполнить аудит.");
    }

    if (status.status === "completed") {
      const data = await apiFetch(`/api/audits/${currentJobId}/results`, {
        signal: pollAbortController.signal
      });
      currentRows = (data.results || []).map(mapApiRow);
      if (!currentAuditStatus?.sitemap_urls?.length) currentRows.unshift(missingSitemapRow());
      currentRows = sortRowsForDisplay(currentRows);
      showResults(currentRows.length);
      return;
    }

    await sleep(POLL_INTERVAL_MS);
  }
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


async function openExistingAudit(jobId, fallbackUrl = null) {
  if (pollAbortController) pollAbortController.abort();
  pollAbortController = new AbortController();

  currentJobId = jobId;
  setAuditContext(jobId, fallbackUrl);
  workspace.hidden = false;
  resultsCard.hidden = true;
  progressCard.hidden = false;
  resetSteps();
  setProgress(0);

  if (fallbackUrl) {
    try {
      const u = new URL(fallbackUrl);
      input.value = u.href;
      $("#audit-host").textContent = u.hostname;
      setAuditContext(jobId, u.href);
    } catch {}
  }

  workspace.scrollIntoView({ behavior: "smooth", block: "start" });

  while (true) {
    if (pollAbortController.signal.aborted) return;

    const status = await apiFetch(`/api/audits/${jobId}`, {
      signal: pollAbortController.signal
    });
    currentAuditStatus = status;
    updateProgressUi(status);

    if (status.normalized_url) {
      try {
        const u = new URL(status.normalized_url);
        input.value = u.href;
        $("#audit-host").textContent = u.hostname;
      } catch {}
    }

    if (status.status === "failed") {
      throw new Error(status.error || "Не удалось выполнить аудит.");
    }

    if (status.status === "completed") {
      const data = await apiFetch(`/api/audits/${jobId}/results`, {
        signal: pollAbortController.signal
      });
      currentRows = (data.results || []).map(mapApiRow);
      if (!currentAuditStatus?.sitemap_urls?.length) currentRows.unshift(missingSitemapRow());
      currentRows = sortRowsForDisplay(currentRows);
      showResults(currentRows.length);
      return;
    }

    await sleep(POLL_INTERVAL_MS);
  }
}

function updateRobotsResult(status) {
  const box = $("#robots-status");
  if (!box || !status || status.robots_found == null) return;

  const title = $("#robots-status-title");
  const text = $("#robots-status-text");
  const url = $("#robots-status-url");
  const icon = $(".robots-status-icon", box);
  const declared = Array.isArray(status.robots_sitemap_urls) ? status.robots_sitemap_urls : [];
  const found = Array.isArray(status.sitemap_urls) ? status.sitemap_urls : [];

  box.hidden = false;
  box.classList.remove("ok", "warn", "error");
  url.textContent = status.robots_url || "";

  if (status.robots_found && declared.length) {
    box.classList.add("ok");
    icon.textContent = "✓";
    title.textContent = "robots.txt найден";
    text.textContent = declared.length === 1
      ? "Sitemap указан в robots.txt."
      : `В robots.txt указано карт сайта: ${declared.length}.`;
  } else if (status.robots_found && found.length) {
    box.classList.add("warn");
    icon.textContent = "!";
    title.textContent = "robots.txt найден";
    text.textContent = "Sitemap в robots.txt не указан, но найден по стандартному адресу.";
  } else if (!status.robots_found && found.length) {
    box.classList.add("warn");
    icon.textContent = "!";
    title.textContent = "robots.txt не найден";
    text.textContent = "Sitemap найден по стандартному адресу. Рекомендуем добавить robots.txt и указать в нём Sitemap.";
  } else if (status.robots_found) {
    box.classList.add("error");
    icon.textContent = "×";
    title.textContent = "robots.txt найден";
    text.textContent = "Sitemap в robots.txt не указан и по стандартным адресам не найден.";
  } else {
    box.classList.add("error");
    icon.textContent = "×";
    title.textContent = "robots.txt не найден";
    text.textContent = "Sitemap также не найден по стандартным адресам.";
  }
}

function showResults(total) {
  updateRobotsResult(currentAuditStatus);
  progressCard.hidden = true;
  resultsCard.hidden = false;

  const errors = currentRows.filter(r => r.status === "error").length;
  const warnings = currentRows.filter(r => r.status === "warning").length;
  const ok = currentRows.filter(r => r.status === "success").length;
  const problems = errors + warnings;

  $("#result-total").textContent = total;
  $("#count-errors").textContent = errors;
  $("#count-warnings").textContent = warnings;
  $("#count-ok").textContent = ok;
  $("#count-all").textContent = total;

  $("#tab-problems").textContent = problems;
  $("#tab-errors").textContent = errors;
  $("#tab-warnings").textContent = warnings;
  $("#tab-ok").textContent = ok;
  $("#tab-all").textContent = total;

  activeFilter = problems ? "problems" : "success";
  visibleLimit = 50;
  $$(".filter-tab").forEach(b => b.classList.toggle("active", b.dataset.filter === activeFilter));
  $$(".summary-card").forEach(b => b.classList.toggle("active-filter", b.dataset.filter === activeFilter));
  renderRows();
  resultsCard.scrollIntoView({ behavior: "smooth", block: "start" });
}

function getFilteredRows() {
  if (activeFilter === "problems") return currentRows.filter(row => row.status !== "success");
  if (activeFilter === "all") return currentRows;
  return currentRows.filter(row => row.status === activeFilter);
}

function renderRows() {
  resultList.innerHTML = "";
  const rows = getFilteredRows();
  const visibleRows = rows.slice(0, visibleLimit);

  emptyState.hidden = rows.length !== 0;
  loadMoreBtn.hidden = rows.length <= visibleLimit;

  visibleRows.forEach(row => {
    const item = document.createElement("article");
    item.className = `result-item status-${row.status}`;
    item.innerHTML = `
      <button class="result-item-main" type="button" aria-expanded="false">
        <span class="result-dot" aria-hidden="true"></span>
        <span class="result-path">${escapeHtml(row.path)}</span>
        <span class="result-message">${escapeHtml(row.message)}</span>
        <span class="result-chevron" aria-hidden="true">⌄</span>
      </button>
      <div class="result-item-detail" hidden></div>
    `;

    const button = $(".result-item-main", item);
    const detailHost = $(".result-item-detail", item);
    button.addEventListener("click", () => toggleDetail(item, detailHost, row, button));
    resultList.appendChild(item);
  });

  if (!loadMoreBtn.hidden) {
    const remaining = rows.length - visibleLimit;
    loadMoreBtn.textContent = `Показать ещё ${Math.min(50, remaining)} из ${remaining}`;
  }
}

function toggleDetail(item, detailHost, row, button) {
  const isOpen = !detailHost.hidden;
  $$(".result-item-detail", resultList).forEach(el => {
    el.hidden = true;
    const parentButton = el.previousElementSibling;
    if (parentButton) parentButton.setAttribute("aria-expanded", "false");
    el.parentElement?.classList.remove("open");
  });
  if (isOpen) return;

  const tpl = $("#detail-template").content.cloneNode(true);
  $(".detail-title", tpl).textContent = row.path;
  const entries = [
    ["URL в sitemap", row.details.originalUrl],
    ["HTTP-код", String(row.details.statusCode)],
    ["Конечный URL", row.details.finalUrl],
    ["Редирект", row.details.redirected ? "Да" : "Нет"]
  ];
  const list = $(".meta-list", tpl);
  entries.forEach(([label, value]) => {
    const line = document.createElement("div");
    line.className = "meta-item";
    line.innerHTML = `<span>${escapeHtml(label)}</span><code>${escapeHtml(value || "—")}</code>`;
    list.appendChild(line);
  });

  const issueBox = $(".issue-box", tpl);
  issueBox.className = `issue-box ${row.status}`;
  issueBox.innerHTML = `<strong>${escapeHtml(row.issueTitle)}</strong><p>${escapeHtml(row.issueText)}</p>`;

  const actions = $(".detail-actions", tpl);
  if (row.pageUrl) {
    const open = document.createElement("a");
    open.className = "secondary-btn link-btn compact-btn";
    open.href = row.pageUrl;
    open.target = "_blank";
    open.rel = "noopener";
    open.textContent = "Открыть страницу ↗";
    actions.appendChild(open);
  }

  detailHost.replaceChildren(tpl);
  detailHost.hidden = false;
  button.setAttribute("aria-expanded", "true");
  item.classList.add("open");
}
function escapeHtml(value) {
  return String(value ?? "—").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  }[ch]));
}
function escapeAttr(value) { return escapeHtml(value); }

if (form) {
  form.addEventListener("submit", async event => {
    event.preventDefault();
    formError.hidden = true;

    const url = normalizeUrl(input.value);
    if (!url) {
      showFormError("Введите корректный адрес сайта, например https://example.ru");
      input.focus();
      return;
    }

    setSubmitting(true);
    try {
      await startAudit(url);
    } catch (error) {
      if (error && error.name === "AbortError") return;
      progressCard.hidden = true;
      workspace.hidden = false;
      showFormError(error?.message || "Не удалось запустить проверку. Попробуйте позже.");
      workspace.hidden = true;
      window.scrollTo({ top: 0, behavior: "smooth" });
    } finally {
      setSubmitting(false);
    }
  });
}

$$(".filter-tab").forEach(btn => {
  btn.addEventListener("click", () => {
    activeFilter = btn.dataset.filter;
    visibleLimit = 50;
    $$(".filter-tab").forEach(b => b.classList.toggle("active", b === btn));
    $$(".summary-card").forEach(b => b.classList.toggle("active-filter", b.dataset.filter === activeFilter));
    renderRows();
  });
});

$$(".summary-card").forEach(btn => {
  btn.addEventListener("click", () => {
    const filter = btn.dataset.filter;
    activeFilter = filter;
    visibleLimit = 50;
    $$(".summary-card").forEach(b => b.classList.toggle("active-filter", b === btn));
    $$(".filter-tab").forEach(b => b.classList.toggle("active", b.dataset.filter === filter));
    renderRows();
    $(".table-toolbar").scrollIntoView({ behavior: "smooth", block: "start" });
  });
});

loadMoreBtn?.addEventListener("click", () => {
  visibleLimit += 50;
  renderRows();
});

$("#restart-btn")?.addEventListener("click", () => {
  if (pollAbortController) pollAbortController.abort();
  currentJobId = null;
  clearAuditContext();
  workspace.hidden = true;
  input.focus();
  window.scrollTo({ top: 0, behavior: "smooth" });
});

$("#download-btn")?.addEventListener("click", () => {
  alert("Экспорт Excel для Sitemap подключим отдельным этапом. Сам Sitemap-аудит уже работает в реальном режиме.");
});

// FAQ accordion: keep at most one answer open at a time.
$$(".faq-list details").forEach(detail => {
  detail.addEventListener("toggle", () => {
    if (!detail.open) return;
    $$(".faq-list details").forEach(other => {
      if (other !== detail && other.open) other.open = false;
    });
  });
});

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
  setAuditContext(initialJob, initialUrl);
  window.setTimeout(async () => {
    try {
      await openExistingAudit(initialJob, initialUrl);
    } catch (error) {
      workspace.hidden = true;
      showFormError(error?.message || "Не удалось открыть результаты аудита.");
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }, 0);
} else if (form && input && initialUrl) {
  input.value = initialUrl;
  window.setTimeout(() => form.requestSubmit(), 0);
}
