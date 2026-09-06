import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { buildSandbox, loadCommon, SITE_DIR } from "./helpers.mjs";

// schema.js defines its rendering helpers and mapApiRow as top-level
// functions; run the real file in a sandbox and read them back out (same
// approach as map-api-row.test.mjs), so these tests exercise the exact code
// that ships - including its escaping.
function loadSchemaModule() {
  const sandbox = buildSandbox();
  loadCommon(sandbox);
  const code = fs.readFileSync(path.join(SITE_DIR, "schema.js"), "utf8");
  vm.runInContext(code, sandbox, { filename: "schema.js" });
  return sandbox;
}

test("renderSchemaObjectsHtml: a simple object renders @type and field/value rows", () => {
  const { renderSchemaObjectsHtml } = loadSchemaModule();
  const html = renderSchemaObjectsHtml([
    { type: "WebSite", properties: [
      { key: "name", value: "GoGoDance" },
      { key: "url", value: "https://gogodance.ru/" },
    ] },
  ], false);
  assert.match(html, /Найденные объекты/);
  assert.match(html, /WebSite/);
  assert.match(html, /name/);
  assert.match(html, /GoGoDance/);
  assert.match(html, /https:\/\/gogodance\.ru\//);
});

test("renderSchemaObjectsHtml: several objects are all rendered", () => {
  const { renderSchemaObjectsHtml } = loadSchemaModule();
  const html = renderSchemaObjectsHtml([
    { type: "WebSite", properties: [{ key: "name", value: "A" }] },
    { type: "Person", properties: [{ key: "name", value: "B" }] },
    { type: "EntertainmentBusiness", properties: [{ key: "name", value: "C" }] },
  ], false);
  assert.match(html, /WebSite/);
  assert.match(html, /Person/);
  assert.match(html, /EntertainmentBusiness/);
});

test("renderSchemaObjectsHtml: sameAs array renders readably, never [object Object]", () => {
  const { renderSchemaObjectsHtml } = loadSchemaModule();
  const html = renderSchemaObjectsHtml([
    { type: "Person", properties: [
      { key: "sameAs", value: ["https://vk.com/x", "https://t.me/y"] },
    ] },
  ], false);
  assert.match(html, /https:\/\/vk\.com\/x/);
  assert.match(html, /https:\/\/t\.me\/y/);
  assert.doesNotMatch(html, /\[object Object\]/);
});

test("renderSchemaObjectsHtml: a nested object (author: Person) is shown inside the parent", () => {
  const { renderSchemaObjectsHtml } = loadSchemaModule();
  const html = renderSchemaObjectsHtml([
    { type: "Article", properties: [
      { key: "author", value: { type: "Person", properties: [
        { key: "name", value: "Иван" },
      ] } },
    ] },
  ], false);
  assert.match(html, /schema-nested/);
  assert.match(html, /Person/);
  assert.match(html, /Иван/);
  assert.doesNotMatch(html, /\[object Object\]/);
});

test("renderSchemaObjectsHtml: BreadcrumbList items are rendered as nested objects", () => {
  const { renderSchemaObjectsHtml } = loadSchemaModule();
  const html = renderSchemaObjectsHtml([
    { type: "BreadcrumbList", properties: [
      { key: "itemListElement", value: [
        { type: "ListItem", properties: [
          { key: "position", value: 1 },
          { key: "name", value: "Главная" },
        ] },
        { type: "ListItem", properties: [
          { key: "position", value: 2 },
          { key: "name", value: "Раздел" },
        ] },
      ] },
    ] },
  ], false);
  assert.match(html, /BreadcrumbList/);
  assert.match(html, /Главная/);
  assert.match(html, /Раздел/);
  assert.match(html, /schema-array/);
});

test("renderSchemaObjectsHtml: XSS in name/description is escaped, not injected", () => {
  const { renderSchemaObjectsHtml } = loadSchemaModule();
  const html = renderSchemaObjectsHtml([
    { type: "<img src=x onerror=alert(1)>", properties: [
      { key: "name", value: "<script>alert('xss')</script>" },
      { key: "description", value: "\"><svg onload=alert(2)>" },
    ] },
  ], false);
  // No raw executable markup survives.
  assert.doesNotMatch(html, /<script>alert/);
  assert.doesNotMatch(html, /<img src=x onerror/);
  assert.doesNotMatch(html, /<svg onload/);
  // The dangerous characters are present only in escaped form.
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /&lt;img/);
});

test("renderSchemaObjectsHtml: a very long value is passed through escaped (backend does the clipping)", () => {
  const { renderSchemaObjectsHtml } = loadSchemaModule();
  const long = "x".repeat(1000);
  const html = renderSchemaObjectsHtml([
    { type: "Thing", properties: [{ key: "description", value: long, truncated: true }] },
  ], false);
  assert.match(html, /x{1000}/);
  // Per-value truncation flag surfaces a short, non-decorative note.
  assert.match(html, /сокращено/);
});

test("renderSchemaObjectsHtml: object-level and page-level truncation are surfaced honestly", () => {
  const { renderSchemaObjectsHtml } = loadSchemaModule();
  const html = renderSchemaObjectsHtml([
    { type: "Thing", properties: [{ key: "name", value: "A" }], truncated: true },
  ], true);
  assert.match(html, /Часть свойств объекта сокращена/);
  assert.match(html, /Показаны не все объекты/);
});

test("renderSchemaObjectsHtml: a large object (>6 props) folds into a <details> block", () => {
  const { renderSchemaObjectsHtml } = loadSchemaModule();
  const properties = Array.from({ length: 9 }, (_, i) => ({ key: `p${i}`, value: `v${i}` }));
  const html = renderSchemaObjectsHtml([{ type: "LocalBusiness", properties }], false);
  assert.match(html, /<details class="schema-object"/);
  assert.match(html, /<summary/);
});

test("renderSchemaObjectsHtml: no objects yields an empty string (section stays hidden)", () => {
  const { renderSchemaObjectsHtml } = loadSchemaModule();
  assert.equal(renderSchemaObjectsHtml([], false), "");
  assert.equal(renderSchemaObjectsHtml(undefined, false), "");
});

test("renderSchemaValue: an empty array shows a dash, not [object Object]", () => {
  const { renderSchemaValue } = loadSchemaModule();
  assert.match(renderSchemaValue([]), /—/);
});

test("mapApiRow: schema objects and truncation flag are passed into details", () => {
  const { mapApiRow } = loadSchemaModule();
  const row = mapApiRow({
    url: "https://example.ru/",
    schema: {
      json_ld_count: 1,
      valid_json_ld_count: 1,
      types: ["WebSite"],
      objects: [{ type: "WebSite", properties: [{ key: "name", value: "X" }] }],
      objects_truncated: true,
    },
  });
  assert.equal(row.details.objects.length, 1);
  assert.equal(row.details.objects[0].type, "WebSite");
  assert.equal(row.details.objectsTruncated, true);
  // Existing counters still flow through unchanged.
  assert.equal(row.details.jsonLdCount, 1);
});

test("mapApiRow: a page without objects gets an empty list, never undefined", () => {
  const { mapApiRow } = loadSchemaModule();
  const row = mapApiRow({ url: "https://example.ru/", schema: { json_ld_count: 0 } });
  assert.ok(Array.isArray(row.details.objects));
  assert.equal(row.details.objects.length, 0);
  assert.equal(row.details.objectsTruncated, false);
});
