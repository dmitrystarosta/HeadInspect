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
  if (page.check_failed) {
    return {
      status: "unavailable",
      path: getPath(page.requested_url || page.url),
      pageUrl: page.url || page.requested_url,
      message: "Не удалось проверить",
      issueTitle: "Страница не проверена",
      issueText: page.check_error?.startsWith("Страница не ответила за")
        ? "Страница не ответила за 30 с. Возможно, сервер отвечает слишком медленно или ограничивает частые автоматические запросы."
        : (page.check_error || "HeadInspect не смог получить страницу."),
      details: {}
    };
  }
  const schema = page.schema || {};
  const errors = Array.isArray(schema.errors) ? schema.errors : [];
  const warnings = Array.isArray(schema.warnings) ? schema.warnings : [];
  const status = errors.length ? "error" : warnings.length ? "warning" : "success";
  const message = errors[0] || warnings[0] || "Schema.org разметка в порядке";
  const allIssues = [...errors, ...warnings];

  return {
    status,
    path: getPath(page.url),
    pageUrl: page.url,
    message,
    issueTitle: status === "error" ? "Найдена ошибка" : status === "warning" ? "Есть замечание" : "Критических замечаний нет",
    issueText: allIssues.length ? allIssues.join(" · ") : "JSON-LD разобран, базовые признаки Schema.org присутствуют.",
    details: {
      jsonLdCount: schema.json_ld_count || 0,
      validCount: schema.valid_json_ld_count || 0,
      invalidCount: schema.invalid_json_ld_count || 0,
      nodeCount: schema.node_count || 0,
      types: Array.isArray(schema.types) ? schema.types : [],
      microdataCount: schema.microdata_count || 0,
      microdataTypes: Array.isArray(schema.microdata_types) ? schema.microdata_types : []
    }
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
    updateProgressUi(status);

    if (status.status === "failed") {
      throw new Error(status.error || "Не удалось выполнить аудит.");
    }

    if (status.status === "completed") {
      const data = await apiFetch(`/api/audits/${currentJobId}/results`, {
        signal: pollAbortController.signal
      });
      currentRows = sortRowsForDisplay((data.results || []).map(mapApiRow));
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
      currentRows = sortRowsForDisplay((data.results || []).map(mapApiRow));
      showResults(currentRows.length);
      return;
    }

    await sleep(POLL_INTERVAL_MS);
  }
}

function showResults(total) {
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

  if (row.status === "unavailable") {
    const list = $(".meta-list", tpl);
    if (list) list.hidden = true;
    const preview = $(".preview-image", tpl);
    if (preview) preview.hidden = true;
    const issueBox = $(".issue-box", tpl);
    issueBox.className = "issue-box unavailable";
    issueBox.innerHTML = `<strong>${escapeHtml(row.issueTitle)}</strong><p>${escapeHtml(row.issueText)}</p>`;
    $(".detail-actions", tpl).innerHTML = row.pageUrl
      ? `<a href="${escapeAttr(row.pageUrl)}" target="_blank" rel="noopener noreferrer">Открыть страницу ↗</a>`
      : "";
    detailHost.innerHTML = "";
    detailHost.appendChild(tpl);
    detailHost.hidden = false;
    item.classList.add("open");
    button.setAttribute("aria-expanded", "true");
    return;
  }
  const typeText = row.details.types.length ? row.details.types.join(", ") : "—";
  const microTypes = row.details.microdataTypes.length ? row.details.microdataTypes.join(", ") : "—";
  const entries = [
    ["JSON-LD блоков", row.details.jsonLdCount],
    ["Валидных JSON-LD", row.details.validCount],
    ["Ошибочных JSON-LD", row.details.invalidCount],
    ["Schema-объектов", row.details.nodeCount],
    ["Типы @type", typeText],
    ["Microdata элементов", row.details.microdataCount],
    ["Microdata itemtype", microTypes]
  ];
  $(".meta-list", tpl).innerHTML = entries.map(([key, value]) => `
    <div class="meta-item"><span>${escapeHtml(key)}</span><div class="meta-value"><code>${escapeHtml(value)}</code></div></div>
  `).join("");
  $(".issue-box", tpl).innerHTML = `<strong>${escapeHtml(row.issueTitle)}</strong>${escapeHtml(row.issueText)}`;
  $(".detail-actions", tpl).innerHTML = `<a href="${escapeAttr(row.pageUrl)}" target="_blank" rel="noopener noreferrer">Открыть страницу ↗</a>`;
  detailHost.innerHTML = "";
  detailHost.appendChild(tpl);
  detailHost.hidden = false;
  item.classList.add("open");
  button.setAttribute("aria-expanded", "true");
}


function lengthHint(value, min, max, label) {
  if (!value || value === "—") {
    return { text: "нет значения", state: "bad" };
  }

  const count = [...String(value)].length;

  if (count < min) {
    return {
      text: `${count} ${pluralRu(count, "символ", "символа", "символов")} — короткий ${label}, ориентир ${min}–${max}`,
      state: "warn"
    };
  }

  if (count > max) {
    return {
      text: `${count} ${pluralRu(count, "символ", "символа", "символов")} — длинный ${label}, ориентир ${min}–${max}`,
      state: "warn"
    };
  }

  return {
    text: `${count} ${pluralRu(count, "символ", "символа", "символов")} — нормальная длина`,
    state: "ok"
  };
}

function pluralRu(n, one, few, many) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
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
  alert("Экспорт Excel для Schema подключим отдельным этапом. Сам Schema-аудит уже работает в реальном режиме.");
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
