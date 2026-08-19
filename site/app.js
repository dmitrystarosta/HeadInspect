const DEMO_ROWS = [
  {
    status: "error",
    path: "/contacts/",
    og: "Есть",
    image: "Нет",
    size: "—",
    weight: "—",
    message: "Нет og:image",
    pageUrl: "https://example.ru/contacts/",
    imageUrl: "",
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
    status: "warning",
    path: "/about/",
    og: "Есть",
    image: "Есть",
    size: "800×800",
    weight: "320 KB",
    message: "Нестандартный размер",
    pageUrl: "https://example.ru/about/",
    imageUrl: "https://example.ru/img/about-og.jpg",
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
    pageUrl: "https://example.ru/services/",
    imageUrl: "https://example.ru/img/services-og.jpg",
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
    status: "success",
    path: "/",
    og: "Есть",
    image: "Есть",
    size: "1200×630",
    weight: "148 KB",
    message: "OK",
    pageUrl: "https://example.ru/",
    imageUrl: "https://example.ru/img/og-main.jpg",
    details: {
      title: "Example — Главная",
      description: "Пример сайта",
      ogTitle: "Example",
      ogDescription: "Пример корректного Open Graph",
      ogImage: "/img/og-main.jpg"
    },
    issueTitle: "Критических замечаний нет",
    issueText: "Основные Open Graph поля присутствуют, изображение доступно."
  },
  {
    status: "success",
    path: "/blog/",
    og: "Есть",
    image: "Есть",
    size: "1200×630",
    weight: "184 KB",
    message: "OK",
    pageUrl: "https://example.ru/blog/",
    imageUrl: "https://example.ru/img/blog-og.jpg",
    details: {
      title: "Блог — Example",
      description: "Статьи и новости",
      ogTitle: "Блог — Example",
      ogDescription: "Статьи и новости компании",
      ogImage: "/img/blog-og.jpg"
    },
    issueTitle: "Критических замечаний нет",
    issueText: "Основные Open Graph поля присутствуют, изображение доступно."
  }
];

const $ = (selector, ctx = document) => ctx.querySelector(selector);
const $$ = (selector, ctx = document) => [...ctx.querySelectorAll(selector)];

const form = $("#audit-form");
const input = $("#site-url");
const formError = $("#form-error");
const workspace = $("#audit-workspace");
const progressCard = $("#progress-card");
const resultsCard = $("#results-card");
const resultBody = $("#result-body");
const emptyState = $("#empty-state");

let activeFilter = "all";
let currentRows = DEMO_ROWS;

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
  const total = 127;
  $("#found-count").textContent = total;
  $("#total-count").textContent = total;
  setStep("urls");
  setProgress(38);

  setStep("scan", "active");
  for (const value of [8, 24, 43, 68, 91, 112, 127]) {
    $("#checked-count").textContent = value;
    setProgress(38 + Math.round(value / total * 62));
    await sleep(230);
  }
  setStep("scan");
  setProgress(100);

  await sleep(350);

  // В прототипе выводим демонстрационные строки.
  currentRows = DEMO_ROWS.map(row => ({
    ...row,
    pageUrl: new URL(row.path, url.origin).href,
    imageUrl: row.imageUrl ? new URL(row.details.ogImage, url.origin).href : ""
  }));

  showResults(total);
}

function showResults(total) {
  progressCard.hidden = true;
  resultsCard.hidden = false;

  const errors = currentRows.filter(r => r.status === "error").length;
  const warnings = currentRows.filter(r => r.status === "warning").length;
  // В демо считаем остальные страницы корректными, даже если не рендерим 127 строк.
  const ok = total - errors - warnings;

  $("#result-total").textContent = total;
  $("#count-errors").textContent = errors;
  $("#count-warnings").textContent = warnings;
  $("#count-ok").textContent = ok;
  $("#count-all").textContent = total;

  $("#tab-all").textContent = total;
  $("#tab-errors").textContent = errors;
  $("#tab-warnings").textContent = warnings;
  $("#tab-ok").textContent = ok;

  activeFilter = "all";
  $$(".filter-tab").forEach(b => b.classList.toggle("active", b.dataset.filter === "all"));
  renderRows();
  resultsCard.scrollIntoView({ behavior: "smooth", block: "start" });
}

function statusLabel(status) {
  return status === "error" ? "Ошибка" : status === "warning" ? "Внимание" : "OK";
}

function renderRows() {
  resultBody.innerHTML = "";
  const rows = currentRows.filter(row => activeFilter === "all" || row.status === activeFilter);

  emptyState.hidden = rows.length !== 0;

  rows.forEach((row, idx) => {
    const tr = document.createElement("tr");
    tr.className = `result-row status-${row.status}`;
    tr.dataset.index = currentRows.indexOf(row);
    tr.innerHTML = `
      <td data-label="Статус"><span class="result-status">${statusLabel(row.status)}</span></td>
      <td data-label="Страница" class="page-cell">${row.path}</td>
      <td data-label="OG">${row.og}</td>
      <td data-label="Изображение">${row.image}</td>
      <td data-label="Размер" class="muted-cell">${row.size}</td>
      <td data-label="Вес" class="muted-cell">${row.weight}</td>
      <td data-label="Результат" class="result-message">${row.message}</td>
    `;
    tr.addEventListener("click", () => toggleDetail(tr, row));
    resultBody.appendChild(tr);
  });
}

function toggleDetail(rowEl, row) {
  const next = rowEl.nextElementSibling;
  if (next?.classList.contains("detail-row")) {
    next.remove();
    return;
  }

  $$(".detail-row", resultBody).forEach(el => el.remove());

  const tpl = $("#detail-template").content.cloneNode(true);
  const detail = $(".detail-row", tpl);
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

  rowEl.after(tpl);
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
    $$(".filter-tab").forEach(b => b.classList.toggle("active", b === btn));
    renderRows();
  });
});

$$(".summary-card").forEach(btn => {
  btn.addEventListener("click", () => {
    const filter = btn.dataset.filter;
    activeFilter = filter;
    $$(".filter-tab").forEach(b => b.classList.toggle("active", b.dataset.filter === filter));
    renderRows();
    $(".table-toolbar").scrollIntoView({ behavior: "smooth", block: "start" });
  });
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
