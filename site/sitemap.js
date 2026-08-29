const {
  $, $$, apiFetch, normalizeUrl, escapeHtml, escapeAttr, getPath,
  setAuditContext, clearAuditContext, createListProgressUi, renderAccessBlocked,
  renderJobExpired, renderPartialNotice, sortRowsForDisplay,
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
let currentAuditStatus = null;

function showFormError(message) {
  formError.textContent = message;
  formError.hidden = false;
}

function setSubmitting(isSubmitting) {
  if (!submitBtn) return;
  submitBtn.disabled = isSubmitting;
  submitBtn.textContent = isSubmitting ? "Запускаем…" : "Проверить сайт";
}

function mapApiRow(page) {
  // access_blocked pages (401/403/429) deliberately fall through to the
  // status_code-driven logic below, NOT the generic "check_failed" branch:
  // Sitemap's job is to report URL availability, and status_code IS known
  // and correct for these (unlike a genuine network/timeout failure, where
  // it's null) - "URL из sitemap недоступен: HTTP 403" is exactly right
  // here, "Без ответа" would understate what Sitemap actually found out.
  if (page.check_failed && page.check_reason !== "access_blocked") {
    if (page.check_reason === "content_type") {
      // URL ответил, но вернул не HTML (например, image/jpeg). Для Sitemap это
      // не «Без ответа»: показываем отдельную понятную ошибку самого URL в карте сайта.
      const contentTypeMatch = /Unexpected content type:\s*([^\s]+)/i.exec(page.check_error || "");
      const contentType = contentTypeMatch ? contentTypeMatch[1] : "не HTML";
      return {
        status: "error",
        path: getPath(page.requested_url || page.url),
        pageUrl: page.url || page.requested_url,
        message: `Не HTML: ${contentType}`,
        issueTitle: "URL ведёт не на HTML-страницу",
        issueText: `URL из sitemap отвечает содержимым ${contentType}. Если это изображение, его лучше указывать через image sitemap / image:loc, а не как отдельную страницу в <loc>.`,
        details: { originalUrl: page.requested_url || page.url, finalUrl: page.url || page.requested_url, statusCode: "ответ получен", redirected: false }
      };
    }

    return {
      status: "unavailable",
      path: getPath(page.requested_url || page.url),
      pageUrl: page.url || page.requested_url,
      message: describeCheckReason(page),
      issueTitle: "Страница не проверена",
      issueText: page.check_reason === "timeout"
        ? `${page.check_error || "Страница не ответила вовремя"}. Возможно, сервер отвечает слишком медленно или ограничивает частые автоматические запросы.`
        : (page.check_error || "HeadInspect не смог получить страницу."),
      details: { originalUrl: page.requested_url || page.url, finalUrl: page.url || page.requested_url, statusCode: "—", redirected: false }
    };
  }
  const originalUrl = page.requested_url || page.url;
  const finalUrl = page.url || originalUrl;
  const statusCode = page.status_code;
  const redirected = originalUrl && finalUrl && originalUrl !== finalUrl;

  let status = "success";
  let message = statusCode ? `HTTP ${statusCode}` : "URL недоступен";
  let issueTitle = "URL доступен";
  let issueText = statusCode === 403
      ? "Сервер вернул HeadInspect HTTP 403 (доступ запрещён). Страница при этом может открываться в обычном браузере, если сайт ограничивает автоматические запросы."
      : (statusCode ? `Страница отвечает HTTP ${statusCode}.` : "Не удалось получить ответ страницы.");

  // Sitemap оценивает только доступность URL и редиректы.
  // Ошибки Open Graph, Meta и Schema относятся к своим модулям и здесь не учитываются.
  if (!statusCode || statusCode >= 400) {
    status = "error";
    message = statusCode ? `HTTP ${statusCode}` : "URL недоступен";
    issueTitle = "URL из sitemap недоступен";
    issueText = statusCode ? `Страница отвечает HTTP ${statusCode}.` : "Не удалось получить ответ страницы.";
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

async function finishAudit(jobId, signal, { trackNormalizedUrl = false } = {}) {
  let status;
  try {
    status = await pollJobUntilDone(jobId, {
      signal,
      onStatus: s => {
        currentAuditStatus = s;
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
  currentRows = (data.results || []).map(mapApiRow);
  if (!currentAuditStatus?.sitemap_urls?.length) currentRows.unshift(missingSitemapRow());
  currentRows = sortRowsForDisplay(currentRows);
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
  currentAuditStatus = null;
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
  box.style.marginTop = "14px";
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
    title.textContent = "Sitemap не указан в robots.txt";
    text.textContent = "Sitemap не указан в robots.txt, но HeadInspect нашёл его по стандартному адресу.";
    url.textContent = found[0] || status.robots_url || "";
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

// Item 5: a sitemap candidate answered (HTTP 200) but HeadInspect could not
// parse it - corrupted gzip, invalid XML, etc. This must be visible, not a
// silent fallback to "just the entry page was audited".
function renderSitemapIssues(status) {
  // Same staleness hazard as HI.renderPartialNotice: always clear a
  // previous job's leftover banner from this reused resultsCard first,
  // regardless of whether the current job has any issues to show.
  $$(".hi-sitemap-issues", resultsCard).forEach(el => el.remove());

  const issues = Array.isArray(status?.sitemap_issues) ? status.sitemap_issues : [];
  if (!issues.length) return;
  const box = document.createElement("div");
  box.className = "issue-box unavailable hi-sitemap-issues";
  box.innerHTML = `<strong>Sitemap найден, но не полностью разобран</strong><ul>${issues.map(issue => `<li>${escapeHtml(issue)}</li>`).join("")}</ul>`;
  resultsCard.prepend(box);
}

function showResults(total, status) {
  updateRobotsResult(currentAuditStatus);
  progressCard.hidden = true;
  resultsCard.hidden = false;
  if (status) {
    renderPartialNotice(resultsCard, status);
    renderSitemapIssues(status);
  }

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
