const {
  $, $$, apiFetch, normalizeUrl, escapeHtml, escapeAttr, pluralRu, getPath,
  setAuditContext, clearAuditContext, createListProgressUi, renderAccessBlocked,
  renderJobExpired, renderPartialNotice, uniqueContentPages, sortRowsForDisplay,
  pollJobUntilDone, describeError, isJobNotFound, describeCheckReason
} = HI;

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

const { setStep, resetSteps, updateProgressUi } = createListProgressUi();

let activeFilter = "problems";
let currentRows = [];
let visibleLimit = 50;
let currentJobId = null;
let pollAbortController = null;

function showFormError(message) {
  formError.textContent = message;
  formError.hidden = false;
}

function setSubmitting(isSubmitting) {
  if (!submitBtn) return;
  submitBtn.disabled = isSubmitting;
  submitBtn.textContent = isSubmitting ? "Запускаем…" : "Проверить сайт";
}

function metaHint(value, type) {
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
      message: describeCheckReason(page),
      issueTitle: "Страница не проверена",
      issueText: page.check_reason === "timeout"
        ? `${page.check_error || "Страница не ответила вовремя"}. Возможно, сервер отвечает слишком медленно или ограничивает частые автоматические запросы.`
        : (page.check_error || "HeadInspect не смог получить страницу."),
      details: {}
    };
  }
  const meta = page.meta || {};
  const errors = Array.isArray(meta.errors) ? meta.errors : [];
  const warnings = Array.isArray(meta.warnings) ? meta.warnings : [];

  const status = errors.length ? "error" : warnings.length ? "warning" : "success";
  const message = errors[0] || warnings[0] || "Meta-теги в порядке";
  const allIssues = [...errors, ...warnings];

  return {
    status,
    path: getPath(page.url),
    pageUrl: page.url,
    message,
    issueTitle: status === "error" ? "Найдена ошибка" : status === "warning" ? "Есть рекомендация" : "Критических замечаний нет",
    issueText: allIssues.length ? allIssues.join(" · ") : "Основные Meta-данные страницы присутствуют, критических замечаний не найдено.",
    details: {
      title: meta.title || "—",
      description: meta.description || "—",
      keywords: meta.keywords || "—",
      robots: meta.robots || "—",
      viewport: meta.viewport || "—",
      lang: meta.lang || "—",
      charset: meta.charset || "—",
      titleCount: meta.title_count ?? 0,
      descriptionCount: meta.description_count ?? 0,
      keywordsCount: meta.keywords_count ?? 0,
      robotsCount: meta.robots_count ?? 0,
      viewportCount: meta.viewport_count ?? 0
    }
  };
}

async function finishAudit(jobId, signal, { trackNormalizedUrl = false } = {}) {
  let status;
  try {
    status = await pollJobUntilDone(jobId, {
      signal,
      onStatus: s => {
        updateProgressUi(s);
        if (trackNormalizedUrl && s.normalized_url) {
          try {
            const u = new URL(s.normalized_url);
            input.value = u.href;
            $("#audit-host").textContent = u.hostname;
          } catch {}
        }
      }
    });
  } catch (error) {
    if (error?.name === "AbortError") return;
    if (isJobNotFound(error)) {
      renderJobExpired({ progressCard, resultsCard }, {
        fallbackUrl: input.value || null,
        onRestart: url => { input.value = url; form?.requestSubmit(); }
      });
      return;
    }
    throw error;
  }

  if (!status) return;
  if (renderAccessBlocked({ progressCard, resultsCard }, status)) return;

  const data = await apiFetch(`/api/audits/${jobId}/results`, { signal });
  currentRows = sortRowsForDisplay(uniqueContentPages(data.results || []).map(mapApiRow));
  showResults(currentRows.length, status);
}

async function startAudit(url) {
  if (pollAbortController) pollAbortController.abort();
  pollAbortController = new AbortController();
  const signal = pollAbortController.signal;

  workspace.hidden = false;
  resultsCard.hidden = true;
  progressCard.hidden = false;
  workspace.scrollIntoView({ behavior: "smooth", block: "start" });

  currentRows = [];
  currentJobId = null;
  resetSteps();
  updateProgressUi({ status: "queued", discovered_urls: 0, checked_urls: 0, progress_percent: 0, normalized_url: null });
  $("#audit-host").textContent = url.hostname;
  setStep("site", "active");

  const created = await apiFetch("/api/audits", {
    method: "POST",
    body: JSON.stringify({ url: url.href }),
    signal
  });

  currentJobId = created.job_id;
  setAuditContext(currentJobId, url.href);
  setSubmitting(false);

  await finishAudit(currentJobId, signal);
}

async function openExistingAudit(jobId, fallbackUrl = null) {
  if (pollAbortController) pollAbortController.abort();
  pollAbortController = new AbortController();
  const signal = pollAbortController.signal;

  currentJobId = jobId;
  setAuditContext(jobId, fallbackUrl);
  workspace.hidden = false;
  resultsCard.hidden = true;
  progressCard.hidden = false;
  resetSteps();
  updateProgressUi({ status: "queued", discovered_urls: 0, checked_urls: 0, progress_percent: 0, normalized_url: null });

  if (fallbackUrl) {
    try {
      const u = new URL(fallbackUrl);
      input.value = u.href;
      $("#audit-host").textContent = u.hostname;
      setAuditContext(jobId, u.href);
    } catch {}
  }

  workspace.scrollIntoView({ behavior: "smooth", block: "start" });

  await finishAudit(jobId, signal, { trackNormalizedUrl: true });
}

function showResults(total, status) {
  progressCard.hidden = true;
  resultsCard.hidden = false;
  if (status) renderPartialNotice(resultsCard, status);

  const errors = currentRows.filter(r => r.status === "error").length;
  const warnings = currentRows.filter(r => r.status === "warning").length;
  const ok = currentRows.filter(r => r.status === "success").length;
  const unavailable = currentRows.filter(r => r.status === "unavailable").length;
  const checked = total - unavailable;
  const problems = errors + warnings;

  $("#result-total").textContent = checked;
  const unavailableSummary = $("#result-unavailable-summary");
  if (unavailableSummary) {
    unavailableSummary.hidden = unavailable === 0;
    $("#result-unavailable").textContent = unavailable;
  }
  $("#count-errors").textContent = errors;
  $("#count-warnings").textContent = warnings;
  $("#count-ok").textContent = ok;
  $("#count-all").textContent = total;

  $("#tab-problems").textContent = problems;
  $("#tab-errors").textContent = errors;
  $("#tab-warnings").textContent = warnings;
  $("#tab-unavailable").textContent = unavailable;
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
  if (activeFilter === "problems") return currentRows.filter(row => row.status === "error" || row.status === "warning");
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

  const titleHint = lengthHint(row.details.title, 30, 60, "title");
  const descriptionHint = lengthHint(row.details.description, 120, 160, "description");
  const keywordsHint = metaHint(row.details.keywords, "keywords");
  const robotsHint = metaHint(row.details.robots, "robots");
  const viewportHint = metaHint(row.details.viewport, "viewport");
  const langHint = metaHint(row.details.lang, "lang");
  const charsetHint = metaHint(row.details.charset, "charset");

  const countHint = (count, label) => count > 1
    ? { text: `${count} ${label} — проверьте дубли в HTML`, state: "warn" }
    : null;

  const entries = [
    { key: "<title>", value: row.details.title, hint: countHint(row.details.titleCount, "тега") || titleHint },
    { key: "description", value: row.details.description, hint: countHint(row.details.descriptionCount, "тега") || descriptionHint },
    { key: "keywords", value: row.details.keywords, hint: countHint(row.details.keywordsCount, "тега") || keywordsHint },
    { key: "robots", value: row.details.robots, hint: countHint(row.details.robotsCount, "тега") || robotsHint },
    { key: "viewport", value: row.details.viewport, hint: countHint(row.details.viewportCount, "тега") || viewportHint },
    { key: "lang", value: row.details.lang, hint: langHint },
    { key: "charset", value: row.details.charset, hint: charsetHint }
  ];

  $(".meta-list", tpl).innerHTML = entries.map(entry => `
    <div class="meta-item">
      <span>${escapeHtml(entry.key)}</span>
      <div class="meta-value">
        <code>${escapeHtml(entry.value)}</code>
        ${entry.hint ? `<small class="length-hint ${entry.hint.state}">${escapeHtml(entry.hint.text)}</small>` : ""}
      </div>
    </div>
  `).join("");

  $(".issue-box", tpl).innerHTML =
    `<strong>${escapeHtml(row.issueTitle)}</strong>${escapeHtml(row.issueText)}`;

  $(".detail-actions", tpl).innerHTML = `
    <a href="${escapeAttr(row.pageUrl)}" target="_blank" rel="noopener noreferrer">Открыть страницу ↗</a>
  `;

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
      const message = describeError(error, "Не удалось запустить проверку. Попробуйте позже.");
      if (message === null) return; // aborted
      progressCard.hidden = true;
      workspace.hidden = false;
      showFormError(message);
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
  alert("Экспорт Excel для Meta подключим отдельным этапом. Сам Meta-аудит уже работает в реальном режиме.");
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
      const message = describeError(error, "Не удалось открыть результаты аудита.");
      if (message === null) return; // aborted
      workspace.hidden = true;
      showFormError(message);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }, 0);
} else if (form && input && initialUrl) {
  input.value = initialUrl;
  window.setTimeout(() => form.requestSubmit(), 0);
}
