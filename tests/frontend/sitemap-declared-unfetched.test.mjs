import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { buildSandbox, loadCommon, SITE_DIR } from "./helpers.mjs";

// missingSitemapRow must tell apart two very different situations that both
// leave sitemap_urls empty:
//   * a sitemap was declared in robots.txt but could not be fetched/parsed
//     -> WARNING ("объявлен, но не получен"), audit fell back to entry page;
//   * no sitemap exists anywhere -> ERROR ("не найден").
// Previously the declared-but-unfetched case showed a green "ok" robots box
// and looked like a healthy one-page site.
function missingSitemapRow() {
  const sandbox = buildSandbox();
  loadCommon(sandbox);
  const code = fs.readFileSync(path.join(SITE_DIR, "sitemap.js"), "utf8");
  vm.runInContext(code, sandbox, { filename: "sitemap.js" });
  return sandbox.missingSitemapRow;
}

test("sitemap: declared-in-robots but unfetched is a warning, not an error", () => {
  const row = missingSitemapRow()({
    robots_found: true,
    robots_sitemap_urls: ["https://example.ru/sitemap.xml"],
    sitemap_urls: [],
    sitemap_issues: ["https://example.ru/sitemap.xml: не удалось получить sitemap (Timeout)"],
  });
  assert.equal(row.status, "warning");
  assert.match(row.message, /объявлен, но не получен/i);
  assert.match(row.issueText, /стартовая страница/i);
});

test("sitemap: genuinely absent sitemap stays an error", () => {
  const row = missingSitemapRow()({
    robots_found: true,
    robots_sitemap_urls: [],
    sitemap_urls: [],
    sitemap_issues: [],
  });
  assert.equal(row.status, "error");
  assert.match(row.message, /не найден/i);
});

test("sitemap: no robots at all still an error", () => {
  const row = missingSitemapRow()({
    robots_found: false,
    robots_sitemap_urls: [],
    sitemap_urls: [],
  });
  assert.equal(row.status, "error");
});
