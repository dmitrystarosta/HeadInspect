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

// The whole point of the Canonical row is that "страница → canonical →
// результат" is legible at a glance, so mapApiRow computes all three parts
// plus a structured detail block for the expanded view.
function mapApiRow(page) {
  if (page.check_failed) {
    return {
      status: "unavailable",
      path: getPath(page.requested_url || page.url),
      pageUrl: page.url || page.requested_url,
      canonicalDisplay: "—",
      verdict: "Страница не проверена",
      message: describeCheckReason(page),
      issueTitle: "Страница не проверена",
      issueText: page.check_reason === "timeout"
        ? `${page.check_error || "Страница не ответила вовремя"}. Возможно, сервер отвечает слишком медленно или ограничивает частые автоматические запросы.`
        : (page.check_error || "HeadInspect не смог получить страницу."),
      details: null
    };
  }

  const c = page.canonical || {};
  const errors = Array.isArray(c.errors) ? c.errors : [];
  const warnings = Array.isArray(c.warnings) ? c.warnings : [];
  const notes = Array.isArray(c.notes) ? c.notes : [];

  const status = errors.length ? "error" : warnings.length ? "warning" : "success";

  let canonicalDisplay = "—";
  if (c.resolved_url) canonicalDisplay = c.resolved_url;
  else if (c.raw_href) canonicalDisplay = c.raw_href;

  const verdict = verdictText(status, c);
  const message = errors[0] || warnings[0] || (c.present ? "Canonical корректен" : "Canonical не указан");
  const allIssues = [...errors, ...warnings];

  return {
    status,
    path: getPath(page.url),
    pageUrl: page.url,
    canonicalDisplay,
    verdict,
    message,
    issueTitle: status === "error" ? "Найдена ошибка" : status === "warning" ? "Есть замечание" : "Замечаний нет",
    issueText: allIssues.length
      ? allIssues.join(" · ")
      : "Canonical страницы корректен, конфликтов не найдено.",
    details: {
      present: !!c.present,
      resolved: c.resolved_url || "—",
      rawHref: c.raw_href || "—",
      source: c.source || "none",
      isRelative: !!c.is_relative,
      isSelf: c.is_self,
      crossDomain: c.cross_domain,
      sameSite: c.same_site,
      hasFragment: !!c.has_fragment,
      hasQuery: !!c.has_query,
      baseUsed: !!c.base_href_used,
      count: c.count ?? 0,
      htmlCount: c.html_count ?? 0,
      headerCount: c.header_count ?? 0,
      targetInAudit: c.target_in_audit,
      targetStatus: c.target_status,
      targetRedirected: c.target_redirected,
      targetFinalUrl: c.target_final_url || null,
      targetNoindex: c.target_noindex,
      chain: Array.isArray(c.chain) ? c.chain : [],
      cycle: !!c.cycle,
      notes
    }
  };
}

function verdictText(status, c) {
  if (!c.present) return "⚠ Canonical не указан";
  if (status === "error") return "✕ Проблема canonical";
  if (status === "warning") return "⚠ Есть замечание";
  if (c.is_self) return "✓ Canonical корректен";
  if (c.cross_domain) return "✓ Canonical на другой домен";
  if (c.same_site) return "✓ Canonical на другую страницу";
  return "✓ Canonical корректен";
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

  // No UI teardown before this call: if the POST rejects (domain cooldown
  // 429, rate limit, queue full, network error), a previously completed
  // report must stay exactly as it was. Only after the backend accepts the
  // new audit do we clear old state and switch to the in-progress view.
  const created = await apiFetch("/api/audits", {
    method: "POST",
    body: JSON.stringify({ url: url.href }),
    signal
  });

  workspace.hidden = false;
  resultsCard.hidden = true;
  progressCard.hidden = false;
  workspace.scrollIntoView({ behavior: "smooth", block: "start" });

  currentRows = [];
  resetSteps();
  updateProgressUi({ status: "queued", discovered_urls: 0, checked_urls: 0, progress_percent: 0, normalized_url: null });
  $("#audit-host").textContent = url.hostname;
  setStep("site", "active");

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
    // Canonical's main entry is always two lines with an identical structure
    // across every card: line 1 is "page path ... verdict", line 2 is the
    // (grey) canonical URL. The canonical URL goes on its own second line
    // regardless of its length so all cards line up the same way.
    item.innerHTML = `
      <button class="result-item-main canonical-main" type="button" aria-expanded="false">
        <span class="result-dot" aria-hidden="true"></span>
        <span class="canonical-flow">
          <span class="canonical-headline">
            <span class="result-path">${escapeHtml(row.path)}</span>
            <span class="canonical-verdict">${escapeHtml(row.verdict)}</span>
          </span>
          <span class="canonical-target-line">
            <span class="canonical-arrow" aria-hidden="true">→</span>
            <span class="canonical-target">${escapeHtml(row.canonicalDisplay)}</span>
          </span>
        </span>
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

function sourceLabel(source) {
  if (source === "both") return "HTML <link> и HTTP-заголовок Link";
  if (source === "header") return "HTTP-заголовок Link";
  if (source === "html") return "HTML <link>";
  return "—";
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

  const d = row.details;
  const entries = [];

  entries.push({ key: "Canonical", value: d.resolved, hint: d.present ? null : { text: "тег не найден", state: "warn" } });
  if (d.present) {
    entries.push({ key: "Источник", value: sourceLabel(d.source), hint: d.source === "both" ? { text: "указан в двух местах", state: "warn" } : null });
    entries.push({ key: "Форма", value: d.isRelative ? "относительный" : "абсолютный", hint: d.isRelative ? { text: `разрешён в ${d.resolved}`, state: "ok" } : null });
    if (d.rawHref && d.rawHref !== d.resolved && d.rawHref !== "—") {
      entries.push({ key: "href как в HTML", value: d.rawHref, hint: null });
    }
    let typeText = "self-canonical";
    if (d.isSelf === false && d.crossDomain) typeText = "на другой домен";
    else if (d.isSelf === false && d.sameSite) typeText = "на другую страницу сайта";
    entries.push({ key: "Тип", value: typeText, hint: d.crossDomain ? { text: "допустимо стандартом", state: "ok" } : null });
    if (d.baseUsed) entries.push({ key: "<base href>", value: "учтён при разрешении", hint: null });
    if (d.hasQuery) entries.push({ key: "Query", value: "есть параметры", hint: { text: "может быть корректно", state: "ok" } });
    if (d.hasFragment) entries.push({ key: "Fragment", value: "есть (#…)", hint: { text: "игнорируется при канонизации", state: "warn" } });
  }

  if (d.targetInAudit === true) {
    entries.push({ key: "Статус цели", value: d.targetStatus != null ? String(d.targetStatus) : "—", hint: (d.targetStatus && d.targetStatus >= 400) ? { text: "страница с ошибкой", state: "bad" } : { text: "проверена в этом аудите", state: "ok" } });
    if (d.targetRedirected) entries.push({ key: "Редирект цели", value: d.targetFinalUrl || "да", hint: { text: "лучше указать конечный URL", state: "warn" } });
    if (d.targetNoindex) entries.push({ key: "noindex цели", value: "да", hint: { text: "конфликт сигналов", state: "bad" } });
  } else if (d.targetInAudit === false) {
    entries.push({ key: "Цель", value: "не проверялась в этом аудите", hint: { text: "информационно", state: "ok" } });
  }

  if (d.cycle) entries.push({ key: "Цепочка", value: d.chain.join(" → "), hint: { text: "цикл canonical", state: "bad" } });
  else if (d.chain.length > 2) entries.push({ key: "Цепочка", value: d.chain.join(" → "), hint: { text: "длинная цепочка", state: "warn" } });

  (d.notes || []).forEach(note => entries.push({ key: "Инфо", value: note, hint: null }));

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
      // startAudit() only tears down the previous view *after* the new audit
      // is accepted, so on any failure here (cooldown 429, rate limit, queue
      // full, network error) whatever was already on screen is untouched.
      showFormError(message);
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
    $(".table-toolbar")?.scrollIntoView({ behavior: "smooth", block: "start" });
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
  alert("Экспорт Excel для Canonical подключим отдельным этапом. Сам Canonical-аудит уже работает в реальном режиме.");
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
