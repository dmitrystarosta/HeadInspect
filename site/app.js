const ISSUE_SEEDS = [
  {
    status: "error",
    path: "/contacts/",
    og: "Есть",
    image: "Нет",
    size: "—",
    weight: "—",
    message: "Нет og:image",
    details: {
      title: "Контакты — Example",
      description: "Контактная информация",
      ogTitle: "Контакты — Example",
      ogDescription: "Свяжитесь с нами",
      ogImage: "—"
    },
    issueTitle: "og:image отсутствует",
    issueText: "Социальные сети и мессенджеры могут выбрать случайное изображение страницы или показать карточку без изображения. Добавьте og:image."
  },
  {
    status: "error",
    path: "/catalog/summer-collection/",
    og: "Есть",
    image: "Есть",
    size: "—",
    weight: "—",
    message: "og:image недоступен",
    details: {
      title: "Летняя коллекция — Example",
      description: "Каталог летней коллекции",
      ogTitle: "Летняя коллекция",
      ogDescription: "Новая летняя коллекция",
      ogImage: "/media/og/summer-2026.jpg"
    },
    issueTitle: "OG image возвращает ошибку",
    issueText: "URL изображения указан в разметке, но файл недоступен. Проверьте путь, редиректы и HTTP-статус изображения."
  },
  {
    status: "warning",
    path: "/about/",
    og: "Есть",
    image: "Есть",
    size: "800×800",
    weight: "320 KB",
    message: "Нестандартный размер",
    details: {
      title: "О компании — Example",
      description: "Рассказываем о компании",
      ogTitle: "О компании — Example",
      ogDescription: "Наша команда и история",
      ogImage: "/img/about-og.jpg"
    },
    issueTitle: "Проверьте формат OG image",
    issueText: "Изображение 800×800 может работать, но для универсальной горизонтальной карточки удобнее формат с соотношением сторон около 1.91:1."
  },
  {
    status: "warning",
    path: "/services/",
    og: "Есть",
    image: "Есть",
    size: "1200×630",
    weight: "1.4 MB",
    message: "Тяжёлое изображение",
    details: {
      title: "Услуги — Example",
      description: "Наши услуги",
      ogTitle: "Услуги — Example",
      ogDescription: "Все услуги компании",
      ogImage: "/img/services-og.jpg"
    },
    issueTitle: "OG image весит 1,4 MB",
    issueText: "Это не критическая ошибка, но изображение стоит оптимизировать, чтобы уменьшить объём загрузки."
  },
  {
    status: "warning",
    path: "/blog/very-long-article-slug-about-site-redesign-and-open-graph/",
    og: "Есть",
    image: "Есть",
    size: "1200×630",
    weight: "740 KB",
    message: "Изображение можно облегчить",
    details: {
      title: "Большая статья — Example",
      description: "Подробный материал",
      ogTitle: "Большая статья — Example",
      ogDescription: "Подробный материал о редизайне",
      ogImage: "/img/article-og.jpg"
    },
    issueTitle: "Вес изображения выше рекомендуемого",
    issueText: "Файл доступен и имеет правильный размер, но его можно дополнительно оптимизировать."
  }
];

const OK_SEED = {
  status: "success",
  og: "Есть",
  image: "Есть",
  size: "1200×630",
  weight: "148 KB",
  message: "OK",
  details: {
    title: "Example — Страница",
    description: "Пример страницы",
    ogTitle: "Example",
    ogDescription: "Корректный Open Graph",
    ogImage: "/img/og-main.jpg"
  },
  issueTitle: "Критических замечаний нет",
  issueText: "Основные Open Graph поля присутствуют, изображение доступно."
};

function buildDemoRows(origin) {
  const errors = Array.from({length: 8}, (_, i) => {
    const seed = ISSUE_SEEDS[i % 2];
    const suffix = i < 2 ? "" : `issue-${String(i + 1).padStart(2, "0")}/`;
    const path = i < 2 ? seed.path : `/section/${suffix}`;
    return {...seed, path};
  });

  const warnings = Array.from({length: 27}, (_, i) => {
    const seed = ISSUE_SEEDS[2 + (i % 3)];
    const path = i < 3 ? seed.path : `/blog/post-${String(i + 1).padStart(3, "0")}/`;
    return {...seed, path};
  });

  const ok = Array.from({length: 452}, (_, i) => ({
    ...OK_SEED,
    path: i === 0 ? "/" : `/page-${String(i + 1).padStart(3, "0")}/`,
    weight: `${120 + (i % 90)} KB`
  }));

  return [...errors, ...warnings, ...ok].map(row => ({
    ...row,
    pageUrl: new URL(row.path, origin).href,
    imageUrl: row.details.ogImage && row.details.ogImage !== "—"
      ? new URL(row.details.ogImage, origin).href
      : ""
  }));
}

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

let activeFilter = "problems";
let currentRows = [];
let visibleLimit = 50;

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
  el.classList.add(state);
  const icon = $("span", el);
  icon.textContent = state === "done" ? "✓" : "◉";
}

function setProgress(percent) {
  $("#progress-percent").textContent = `${percent}%`;
  $("#progress-bar").style.width = `${percent}%`;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function runDemoAudit(url) {
  workspace.hidden = false;
  resultsCard.hidden = true;
  progressCard.hidden = false;
  workspace.scrollIntoView({ behavior: "smooth", block: "start" });

  $("#audit-host").textContent = url.hostname;
  $("#found-count").textContent = "0";
  $("#checked-count").textContent = "0";
  $("#total-count").textContent = "0";
  setProgress(0);

  $$(".step").forEach(el => {
    el.classList.remove("done", "active");
    $("span", el).textContent = "○";
  });

  setStep("site", "active");
  await sleep(450);
  setStep("site");
  setProgress(10);

  setStep("robots", "active");
  await sleep(500);
  setStep("robots");
  setProgress(20);

  setStep("sitemap", "active");
  await sleep(550);
  setStep("sitemap");
  setProgress(30);

  setStep("urls", "active");
  await sleep(500);
  const total = 487;
  $("#found-count").textContent = total;
  $("#total-count").textContent = total;
  setStep("urls");
  setProgress(38);

  setStep("scan", "active");
  for (const value of [24, 81, 146, 233, 319, 411, 487]) {
    $("#checked-count").textContent = value;
    setProgress(38 + Math.round(value / total * 62));
    await sleep(230);
  }
  setStep("scan");
  setProgress(100);

  await sleep(350);

  // В прототипе имитируем крупный аудит: 487 страниц,
  // чтобы интерфейс сразу был рассчитан на реальную нагрузку.
  currentRows = buildDemoRows(url.origin);
  showResults(currentRows.length);
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

  activeFilter = "problems";
  visibleLimit = 50;
  $$(".filter-tab").forEach(b => b.classList.toggle("active", b.dataset.filter === "problems"));
  $$(".summary-card").forEach(b => b.classList.remove("active-filter"));
  renderRows();
  resultsCard.scrollIntoView({ behavior: "smooth", block: "start" });
}

function statusLabel(status) {
  return status === "error" ? "Ошибка" : status === "warning" ? "Внимание" : "OK";
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

  visibleRows.forEach((row) => {
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

  const meta = $(".meta-list", tpl);
  const entries = [
    ["<title>", row.details.title],
    ["description", row.details.description],
    ["og:title", row.details.ogTitle],
    ["og:description", row.details.ogDescription],
    ["og:image", row.details.ogImage]
  ];
  meta.innerHTML = entries.map(([k,v]) =>
    `<div class="meta-item"><span>${escapeHtml(k)}</span><code>${escapeHtml(v)}</code></div>`
  ).join("");

  $(".issue-box", tpl).innerHTML = `<strong>${escapeHtml(row.issueTitle)}</strong>${escapeHtml(row.issueText)}`;

  const actions = $(".detail-actions", tpl);
  actions.innerHTML = `
    <a href="${escapeAttr(row.pageUrl)}" target="_blank" rel="noopener noreferrer">Открыть страницу ↗</a>
    ${row.imageUrl ? `<a href="${escapeAttr(row.imageUrl)}" target="_blank" rel="noopener noreferrer">Открыть изображение ↗</a>` : ""}
  `;

  if (row.size !== "—") $(".preview-image span", tpl).textContent = row.size;
  else $(".preview-box", tpl).hidden = true;

  detailHost.innerHTML = "";
  detailHost.appendChild(tpl);
  detailHost.hidden = false;
  item.classList.add("open");
  button.setAttribute("aria-expanded", "true");
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, ch => ({
    "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#039;"
  }[ch]));
}
function escapeAttr(value) { return escapeHtml(value); }

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.hidden = true;

  const url = normalizeUrl(input.value);
  if (!url) {
    formError.textContent = "Введите корректный адрес сайта, например https://example.ru";
    formError.hidden = false;
    input.focus();
    return;
  }

  await runDemoAudit(url);
});

$$(".filter-tab").forEach(btn => {
  btn.addEventListener("click", () => {
    activeFilter = btn.dataset.filter;
    visibleLimit = 50;
    $$(".filter-tab").forEach(b => b.classList.toggle("active", b === btn));
    renderRows();
  });
});

$$(".summary-card").forEach(btn => {
  btn.addEventListener("click", () => {
    const filter = btn.dataset.filter;
    activeFilter = filter;
    visibleLimit = 50;
    $$(".filter-tab").forEach(b => b.classList.toggle("active", b.dataset.filter === filter));
    renderRows();
    $(".table-toolbar").scrollIntoView({ behavior: "smooth", block: "start" });
  });
});

loadMoreBtn.addEventListener("click", () => {
  visibleLimit += 50;
  renderRows();
});

$("#restart-btn").addEventListener("click", () => {
  workspace.hidden = true;
  input.focus();
  window.scrollTo({ top: 0, behavior: "smooth" });
});

$("#download-btn").addEventListener("click", () => {
  alert("В этой фронтенд-версии кнопка показана как часть интерфейса. Экспорт XLSX подключим вместе с backend.");
});


// FAQ accordion: keep at most one answer open at a time.
$$(".faq-list details").forEach((detail) => {
  detail.addEventListener("toggle", () => {
    if (!detail.open) return;
    $$(".faq-list details").forEach((other) => {
      if (other !== detail && other.open) other.open = false;
    });
  });
});

const menuToggle = $(".menu-toggle");
const nav = $(".main-nav");
menuToggle.addEventListener("click", () => {
  const open = nav.classList.toggle("open");
  menuToggle.setAttribute("aria-expanded", String(open));
});
$$(".main-nav a").forEach(a => a.addEventListener("click", () => {
  nav.classList.remove("open");
  menuToggle.setAttribute("aria-expanded", "false");
}));
