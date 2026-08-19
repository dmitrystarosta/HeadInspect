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

function bytesLabel(bytes) {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function sizeLabel(og) {
  return og.image_width && og.image_height ? `${og.image_width}×${og.image_height}` : "—";
}

function mapApiRow(page) {
  const errors = Array.isArray(page.errors) ? page.errors : [];
  const warnings = Array.isArray(page.warnings) ? page.warnings : [];
  const og = page.open_graph || {};

  const status = errors.length ? "error" : warnings.length ? "warning" : "success";
  const message = errors[0] || warnings[0] || "Open Graph в порядке";
  const allIssues = [...errors, ...warnings];

  return {
    status,
    path: getPath(page.url),
    pageUrl: page.url,
    imageUrl: og.image || "",
    size: sizeLabel(og),
    weight: bytesLabel(og.image_bytes),
    message,
    issueTitle: status === "error" ? "Найдена ошибка" : status === "warning" ? "Есть замечание" : "Критических замечаний нет",
    issueText: allIssues.length ? allIssues.join(" · ") : "Основные Open Graph-поля присутствуют, а og:image доступен.",
    details: {
      title: page.title || "—",
      description: page.meta_description || "—",
      ogTitle: og.title || "—",
      ogDescription: og.description || "—",
      ogUrl: og.url || "—",
      ogType: og.type || "—",
      ogImage: og.image || "—",
      imageStatus: og.image_status_code ?? "—",
      imageType: og.image_content_type || "—",
      imageFormat: og.image_format || "—",
      imageSize: sizeLabel(og),
      imageWeight: bytesLabel(og.image_bytes)
    }
  };
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
      currentRows = (data.results || []).map(mapApiRow);
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

  const entries = [
    ["<title>", row.details.title],
    ["description", row.details.description],
    ["og:title", row.details.ogTitle],
    ["og:description", row.details.ogDescription],
    ["og:url", row.details.ogUrl],
    ["og:type", row.details.ogType],
    ["og:image", row.details.ogImage],
    ["image HTTP", row.details.imageStatus],
    ["image MIME", row.details.imageType],
    ["image format", row.details.imageFormat],
    ["image size", row.details.imageSize],
    ["image weight", row.details.imageWeight]
  ];

  $(".meta-list", tpl).innerHTML = entries.map(([k, v]) =>
    `<div class="meta-item"><span>${escapeHtml(k)}</span><code>${escapeHtml(v)}</code></div>`
  ).join("");

  $(".issue-box", tpl).innerHTML =
    `<strong>${escapeHtml(row.issueTitle)}</strong>${escapeHtml(row.issueText)}`;

  $(".detail-actions", tpl).innerHTML = `
    <a href="${escapeAttr(row.pageUrl)}" target="_blank" rel="noopener noreferrer">Открыть страницу ↗</a>
    ${row.imageUrl ? `<a href="${escapeAttr(row.imageUrl)}" target="_blank" rel="noopener noreferrer">Открыть изображение ↗</a>` : ""}
  `;

  const preview = $(".preview-image", tpl);
  if (row.imageUrl) {
    preview.innerHTML = `<img src="${escapeAttr(row.imageUrl)}" alt="" loading="lazy"><span>${escapeHtml(row.size)} · ${escapeHtml(row.weight)}</span>`;
  } else {
    $(".preview-box", tpl).hidden = true;
  }

  detailHost.innerHTML = "";
  detailHost.appendChild(tpl);
  detailHost.hidden = false;
  item.classList.add("open");
  button.setAttribute("aria-expanded", "true");
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
  workspace.hidden = true;
  input.focus();
  window.scrollTo({ top: 0, behavior: "smooth" });
});

$("#download-btn")?.addEventListener("click", () => {
  alert("Экспорт Excel подключим следующим этапом. Сам аудит Open Graph уже работает в реальном режиме.");
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


const initialUrl = new URLSearchParams(window.location.search).get("url");
if (form && input && initialUrl) {
  input.value = initialUrl;
  window.setTimeout(() => form.requestSubmit(), 0);
}
