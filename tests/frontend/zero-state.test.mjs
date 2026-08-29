import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import fs from "node:fs";
import path from "node:path";
import { buildSandbox, buildRegistryDocument, loadCommon, makeEl, SITE_DIR } from "./helpers.mjs";

// Item 1: "0 ошибок" (numeric) and "Нет ошибок" (text) must never both be
// possible outcomes for the same zero state - and specifically, showing
// "0 ошибок" at all is the bug. Real trigger: errors === 0 but warnings > 0
// (common for Open Graph/Meta), which the old combined `errors || warnings`
// condition pushed into the all-numeric branch.

function buildModuleTile(moduleName) {
  const dot = makeEl("span");
  dot.classList.add("module-status");
  const counts = makeEl("div");
  counts.classList.add("module-counts");
  const bodyP = makeEl("p");
  const bodyA = makeEl("a");
  const body = makeEl("div");
  body.classList.add("audit-module-body");
  body.appendChild(bodyP);
  body.appendChild(bodyA);

  const module = makeEl("div");
  module.classList.add("audit-module");
  module.attributes["data-module"] = moduleName;
  module.appendChild(dot);
  module.appendChild(counts);
  module.appendChild(body);

  return { module, dot, counts, bodyP, bodyA };
}

function loadHome(sandbox) {
  const code = fs.readFileSync(path.join(SITE_DIR, "home.js"), "utf8");
  vm.runInContext(code, sandbox, { filename: "home.js" });
}

function makePage(i, { status = 200, ogErrors = [], ogWarnings = [] } = {}) {
  const url = `https://example.ru/p${i}`;
  return {
    url, requested_url: url, status_code: status, check_failed: false,
    errors: ogErrors, warnings: ogWarnings,
    open_graph: {}, meta: {}, schema: {},
  };
}

function setupHomeWithModules() {
  const tiles = {
    "open-graph": buildModuleTile("open-graph"),
    meta: buildModuleTile("meta"),
    schema: buildModuleTile("schema"),
    sitemap: buildModuleTile("sitemap"),
  };
  const registry = {
    "home-audit-form": makeEl("form"),
    "home-site-url": makeEl("input"),
    "home-form-error": makeEl("div"),
    "home-audit-workspace": makeEl("div"),
    "home-progress-card": makeEl("div"),
    "home-results-card": makeEl("div"),
    "home-audit-host": makeEl("span"),
    "home-found-count": makeEl("b"),
    "home-checked-count": makeEl("b"),
    "home-total-count": makeEl("b"),
    "home-progress-percent": makeEl("strong"),
    "home-progress-bar": makeEl("div"),
    "home-restart-btn": makeEl("button"),
    "home-result-total": (() => {
      const strong = makeEl("strong");
      const p = makeEl("p");
      p.appendChild(strong);
      return strong;
    })(),
  };
  const moduleRegistry = Object.fromEntries(
    Object.entries(tiles).map(([name, t]) => [name, t.module])
  );
  const documentImpl = buildRegistryDocument(registry, moduleRegistry);
  const sandbox = buildSandbox({ documentImpl });
  loadCommon(sandbox);
  loadHome(sandbox);
  return { sandbox, tiles };
}

test("home.js module tile: zero errors + nonzero warnings never shows literal '0 ошибок' (the actual production bug)", () => {
  const { sandbox, tiles } = setupHomeWithModules();
  // Open Graph: 0 errors, several warnings - exactly the bionicashow.ru shape.
  const pages = [
    makePage(0, { ogWarnings: ["Нет og:description"] }),
    makePage(1, { ogWarnings: ["Нет og:url"] }),
  ];
  const status = { normalized_url: "https://example.ru/", sitemap_urls: ["https://example.ru/sitemap.xml"] };

  sandbox.renderAuditModule("open-graph", pages, status, "job-1");

  const html = tiles["open-graph"].counts.innerHTML;
  assert.doesNotMatch(html, /0 ошибок/, "must never render literal '0 ошибок'");
  assert.match(html, /Нет ошибок/);
  assert.match(html, />2 предупреждения</);
});

test("home.js module tile: zero warnings + nonzero errors shows 'Нет предупреждений', not '0 предупреждений'", () => {
  const { sandbox, tiles } = setupHomeWithModules();
  const pages = [makePage(0, { ogErrors: ["Нет og:image"] })];
  const status = { normalized_url: "https://example.ru/", sitemap_urls: ["https://example.ru/sitemap.xml"] };

  sandbox.renderAuditModule("open-graph", pages, status, "job-1");

  const html = tiles["open-graph"].counts.innerHTML;
  assert.doesNotMatch(html, /0 предупреждений/);
  assert.match(html, /Нет предупреждений/);
  assert.match(html, />1 ошибка</);
});

test("home.js module tile: both zero shows both text states, styled with the existing green success class", () => {
  const { sandbox, tiles } = setupHomeWithModules();
  const pages = [makePage(0)];
  const status = { normalized_url: "https://example.ru/", sitemap_urls: ["https://example.ru/sitemap.xml"] };

  sandbox.renderAuditModule("schema", pages, status, "job-1");

  const html = tiles.schema.counts.innerHTML;
  assert.match(html, /Нет ошибок/);
  assert.match(html, /Нет предупреждений/);
  // Reuses the existing "module-ok" success style - no new CSS introduced.
  assert.match(html, /class="module-ok"/);
});

test("home.js module tile: nonzero errors AND warnings both stay fully numeric (regression guard)", () => {
  const { sandbox, tiles } = setupHomeWithModules();
  const pages = [
    makePage(0, { ogErrors: ["Нет og:image"] }),
    makePage(1, { ogWarnings: ["Нет og:url"] }),
  ];
  const status = { normalized_url: "https://example.ru/", sitemap_urls: ["https://example.ru/sitemap.xml"] };

  sandbox.renderAuditModule("open-graph", pages, status, "job-1");

  const html = tiles["open-graph"].counts.innerHTML;
  assert.doesNotMatch(html, /Нет ошибок|Нет предупреждений/);
  assert.match(html, />1 ошибка</);
  assert.match(html, />1 предупреждение</);
});
