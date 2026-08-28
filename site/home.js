const {
  $, $$, apiFetch, normalizeUrl, pluralRu, setHomeAuditUrl, createStepper,
  renderAccessBlocked, renderJobExpired, renderPartialNotice, uniqueContentPages,
  pollJobUntilDone, describeError, isJobNotFound
} = HI;

const form = $("#home-audit-form");
const input = $("#home-site-url");
const formError = $("#home-form-error");
const workspace = $("#home-audit-workspace");
const progressCard = $("#home-progress-card");
const resultsCard = $("#home-results-card");
const submitBtn = form ? $('button[type="submit"]', form) : null;

const { setStep, resetSteps } = createStepper("data-home-step");

let pollAbortController = null;

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
  if (status.sitemap_urls?.length || status.status === "running" || status.status === "completed" || status.status === "completed_partial") setStep("sitemap", "done");
  if (discovered > 0) setStep("urls", "done");
  if (status.status === "running") setStep("scan", "active");
  if (status.status === "completed") {
    setStep("scan", "done");
    setProgress(100);
  } else if (status.status === "completed_partial") {
    setStep("scan", "done");
  }
}

function moduleTotals(pages, moduleName) {
  // Sitemap работает с каждым URL из карты сайта, включая редиректы.
  // Контентные модули считают каждую конечную страницу только один раз.
  if (moduleName !== "sitemap") pages = uniqueContentPages(pages);
  pages = pages.filter(page => !page.check_failed);
  if (moduleName === "meta") {
    return pages.reduce((totals, page) => {
      const meta = page.meta || {};
      totals.errors += Array.isArray(meta.errors) ? meta.errors.length : 0;
      totals.warnings += Array.isArray(meta.warnings) ? meta.warnings.length : 0;
      return totals;
    }, { errors: 0, warnings: 0 });
  }

  if (moduleName === "schema") {
    return pages.reduce((totals, page) => {
      const schema = page.schema || {};
      totals.errors += Array.isArray(schema.errors) ? schema.errors.length : 0;
      totals.warnings += Array.isArray(schema.warnings) ? schema.warnings.length : 0;
      return totals;
    }, { errors: 0, warnings: 0 });
  }

  if (moduleName === "sitemap") {
    return pages.reduce((totals, page) => {
      const statusCode = page.status_code;
      const redirected = page.requested_url && page.url && page.requested_url !== page.url;

      // Sitemap оценивает только доступность URL и редиректы.
      // Ошибки Open Graph, Meta и Schema здесь не учитываются.
      if (!statusCode || statusCode >= 400) totals.errors += 1;
      else if (redirected || (statusCode >= 300 && statusCode < 400)) totals.warnings += 1;
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

  let { errors, warnings } = moduleTotals(pages, moduleName);
  if (moduleName === "sitemap" && !status.sitemap_urls?.length) errors += 1;
  const dot = $(".module-status", module);
  const counts = $(".module-counts", module);
  const bodyText = $(".audit-module-body p", module);
  const link = $(".audit-module-body a", module);

  dot?.classList.remove("error", "warning", "success", "soon");
  dot?.classList.add(errors ? "error" : warnings ? "warning" : "success");

  if (counts) {
    if (errors || warnings) {
      counts.innerHTML = `<strong>${errors} ${pluralRu(errors, "ошибка", "ошибки", "ошибок")}</strong><small>${warnings} ${pluralRu(warnings, "предупреждение", "предупреждения", "предупреждений")}</small>`;
    } else {
      counts.innerHTML = `<strong class="module-ok">Ошибок нет</strong><small>Предупреждений нет</small>`;
    }
  }

  const failedChecks = pages.filter(page => page.check_failed).length;
  const successfullyChecked = pages.length - failedChecks;
  const checkedText = moduleName === "meta"
    ? `Meta-теги проверены на ${successfullyChecked} страницах.`
    : moduleName === "schema"
      ? `Schema.org проверена на ${successfullyChecked} страницах.`
      : moduleName === "sitemap"
        ? `Sitemap проверен: ${successfullyChecked} URL.`
        : `Open Graph проверен на ${successfullyChecked} страницах.`;

  if (bodyText) {
    const unavailableText = failedChecks ? ` Не удалось проверить: ${failedChecks}. Эти страницы не считаются ошибками сайта.` : "";
    bodyText.textContent = errors || warnings
      ? `${checkedText} Найдено ошибок: ${errors}, предупреждений: ${warnings}.${unavailableText}`
      : `${checkedText} Ошибок и предупреждений не найдено.${unavailableText}`;
  }

  if (link && status.normalized_url) {
    const basePath = moduleName === "meta"
      ? "/meta/"
      : moduleName === "schema"
        ? "/schema/"
        : moduleName === "sitemap"
          ? "/sitemap/"
          : "/open-graph/";
    link.href = `${basePath}?job=${encodeURIComponent(jobId)}&url=${encodeURIComponent(status.normalized_url)}`;
    link.textContent = moduleName === "meta"
      ? "Открыть подробный Meta-аудит"
      : moduleName === "schema"
        ? "Открыть подробный Schema-аудит"
        : moduleName === "sitemap"
          ? "Открыть подробный Sitemap-аудит"
          : "Открыть подробный Open Graph-аудит";
  }
}

function renderHomeResult(status, results, jobId) {
  const pages = results?.results || [];
  // Use unique final-URL pages (same de-duplication OG/Meta/Schema already
  // apply) rather than trusting the backend's raw checked_urls/failed_checks
  // counters directly: those counters can include pages that were merely
  // *attempted* (including, historically, extra attempts made right around
  // a mid-audit block being detected), which is not the same thing as
  // "unique URLs of this audit that could or couldn't be checked". Deriving
  // both numbers from the same page list the modules already use keeps the
  // home page self-consistent with them by construction.
  const uniquePages = uniqueContentPages(pages);
  const failedChecks = uniquePages.filter(page => page.check_failed).length;
  const checkedOk = uniquePages.length - failedChecks;

  const resultSummary = $("#home-result-total")?.parentElement;
  if (resultSummary) {
    // Always rewrite the whole summary line, never only conditionally: a
    // job with zero failures must not be left showing a previous job's
    // "не удалось проверить: N" text just because this run had nothing to
    // add there.
    resultSummary.innerHTML = failedChecks
      ? `<strong id="home-result-total">${checkedOk}</strong> страниц проверено · не удалось проверить: ${failedChecks}`
      : `<strong id="home-result-total">${checkedOk}</strong> страниц проверено`;
  } else {
    const totalEl = $("#home-result-total");
    if (totalEl) totalEl.textContent = checkedOk;
  }

  renderAuditModule("open-graph", pages, status, jobId);
  renderAuditModule("meta", pages, status, jobId);
  renderAuditModule("schema", pages, status, jobId);
  renderAuditModule("sitemap", pages, status, jobId);

  progressCard.hidden = true;
  resultsCard.hidden = false;
  renderPartialNotice(resultsCard, status);
  resultsCard.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function finishAudit(jobId, signal, { trackNormalizedUrl = false } = {}) {
  let status;
  try {
    status = await pollJobUntilDone(jobId, {
      signal,
      onStatus: s => {
        updateProgress(s);
        if (trackNormalizedUrl && s.normalized_url) {
          input.value = s.normalized_url;
          setHomeAuditUrl(jobId, s.normalized_url);
          try {
            const u = new URL(s.normalized_url);
            $("#home-audit-host").textContent = u.hostname;
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

  const results = await apiFetch(`/api/audits/${jobId}/results`, { signal });
  renderHomeResult(status, results, jobId);
}

async function startAudit(url) {
  if (pollAbortController) pollAbortController.abort();
  pollAbortController = new AbortController();
  const signal = pollAbortController.signal;

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
    signal
  });
  setHomeAuditUrl(created.job_id, url.href);
  setSubmitting(false);

  await finishAudit(created.job_id, signal);
}

async function openExistingHomeAudit(jobId, fallbackUrl = null) {
  if (pollAbortController) pollAbortController.abort();
  pollAbortController = new AbortController();
  const signal = pollAbortController.signal;

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

  await finishAudit(jobId, signal, { trackNormalizedUrl: true });
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
    const message = describeError(error, "Не удалось запустить проверку. Попробуйте позже.");
    if (message === null) return; // aborted
    workspace.hidden = true;
    showError(message);
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
      const message = describeError(error, "Не удалось открыть результаты общего аудита.");
      if (message === null) return; // aborted
      workspace.hidden = true;
      showError(message);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }, 0);
} else if (form && input && initialUrl) {
  input.value = initialUrl;
}
