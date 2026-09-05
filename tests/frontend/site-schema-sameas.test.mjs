import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { SITE_DIR } from "./helpers.mjs";

// HeadInspect's own Schema.org markup must advertise the project's official
// communities via sameAs on the site's stable WebSite entity
// (https://headinspect.ru/#website), which is present identically on every
// page. This guards against (a) the JSON-LD being broken by an edit, and
// (b) sameAs disappearing or drifting onto the wrong entity.
const PAGES = [
  "index.html",
  "open-graph/index.html",
  "meta/index.html",
  "schema/index.html",
  "sitemap/index.html",
  "canonical/index.html",
  "privacy/index.html",
];

const EXPECTED_SAMEAS = ["https://vk.ru/headinspect", "https://t.me/headinspect"];
const WEBSITE_ID = "https://headinspect.ru/#website";

function loadGraph(page) {
  const html = fs.readFileSync(path.join(SITE_DIR, page), "utf8");
  const m = html.match(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/);
  assert.ok(m, `${page}: JSON-LD block present`);
  const data = JSON.parse(m[1]); // throws if the markup was broken
  assert.ok(Array.isArray(data["@graph"]), `${page}: @graph present`);
  return data["@graph"];
}

for (const page of PAGES) {
  test(`${page}: WebSite entity carries the official sameAs links`, () => {
    const graph = loadGraph(page);
    const website = graph.find(n => n["@id"] === WEBSITE_ID);
    assert.ok(website, `${page}: WebSite (#website) entity present`);
    assert.equal(website["@type"], "WebSite");
    assert.deepEqual(website.sameAs, EXPECTED_SAMEAS);
    // Relations must stay intact: WebSite still points at its creator.
    assert.equal(website.publisher?.["@id"], "https://headinspect.ru/#creator");
  });

  test(`${page}: sameAs is not duplicated onto other entities`, () => {
    const graph = loadGraph(page);
    const withSameAs = graph.filter(n => "sameAs" in n).map(n => n["@id"]);
    assert.deepEqual(withSameAs, [WEBSITE_ID]);
  });
}
