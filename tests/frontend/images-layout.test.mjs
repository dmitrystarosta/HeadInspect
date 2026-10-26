import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { SITE_DIR } from "./helpers.mjs";

// The test harness has no layout engine, so a true overflow test isn't
// possible. This is a structural regression guard: it asserts the dedicated,
// scoped Images-row CSS that prevents a long image URL from overlapping the
// adjacent HTTP/size/characteristics column stays in place (wrapping flex row,
// min-width:0, overflow-wrap, and a narrow-screen stack). Paired with the
// markup contract in images.js (URL in a <span>, characteristics in
// .meta-value) exercised by the other Images tests.
const css = fs.readFileSync(path.join(SITE_DIR, "styles.css"), "utf8");

function blockFor(selector) {
  const idx = css.indexOf(selector);
  assert.notEqual(idx, -1, `missing CSS rule: ${selector}`);
  const open = css.indexOf("{", idx);
  const close = css.indexOf("}", open);
  return css.slice(open + 1, close);
}

test("Images rows use a wrapping flex layout scoped to .images-list", () => {
  const item = blockFor(".images-list .meta-item");
  assert.match(item, /display:\s*flex/);
  assert.match(item, /flex-wrap:\s*wrap/);
});

test("URL cell keeps a minimum width so it never breaks one char per line", () => {
  const span = blockFor(".images-list .meta-item > span");
  // Container-robust minimum (never 0, never wider than the row) is what
  // prevented the character-per-line breakage in a narrow results column.
  assert.match(span, /min-width:\s*min\(\s*100%\s*,\s*14rem\s*\)/);
  assert.match(span, /overflow-wrap:\s*anywhere/);
});

test("Characteristics column can shrink and is pushed to the right edge", () => {
  const value = blockFor(".images-list .meta-value");
  assert.match(value, /min-width:\s*0/);
  assert.match(value, /margin-left:\s*auto/);
});

test("On a small viewport the characteristics stack and left-align under the URL", () => {
  assert.match(css, /@media \(max-width: 640px\) \{\s*\.images-list \.meta-value \{[^}]*flex-basis:\s*100%/);
});
