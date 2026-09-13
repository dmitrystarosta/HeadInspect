import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { SITE_DIR } from "./helpers.mjs";

// The "big sites can take a few minutes" reassurance must live INSIDE
// #home-progress-card (which home.js hides on every terminal state), so it is
// shown only while the general audit is in progress and never after it ends.
// It must not leak into the results card, and must be styled with the shared
// muted token so it follows the dark theme.

const html = fs.readFileSync(path.join(SITE_DIR, "index.html"), "utf8");
const css = fs.readFileSync(path.join(SITE_DIR, "styles.css"), "utf8");
const homeJs = fs.readFileSync(path.join(SITE_DIR, "home.js"), "utf8");

// The progress card is emitted as a single line; slice from its opening to the
// end of that line (it is the last element on the line).
const progressLine = html.split("\n").find(l => l.includes('id="home-progress-card"'));
const card = progressLine.slice(progressLine.indexOf('id="home-progress-card"'));

test("long-audit hint lives inside the progress card with the expected message", () => {
  assert.ok(progressLine, "#home-progress-card should exist in index.html");
  assert.match(card, /class="progress-hint"/);
  assert.match(card, /несколько минут/);
  assert.match(card, /Сервис продолжает работать/);
});

test("hint is not duplicated into the results (post-completion) view", () => {
  // Exactly one hint in the whole page, and it is the one inside the card.
  const occurrences = (html.match(/progress-hint/g) || []).length;
  assert.equal(occurrences, 1, "hint must appear once, only inside the progress card");
  const resultsLine = html.split("\n").find(l => l.includes('id="home-results-card"'));
  if (resultsLine) assert.ok(!resultsLine.includes("progress-hint"));
});

test("progress card (and thus the hint) is hidden when the audit finishes", () => {
  // renderHomeResult hides the whole card on completion, so the hint disappears
  // with it - no separate show/hide logic needed for the hint itself.
  assert.match(homeJs, /progressCard\.hidden = true/);
});

test("hint uses the shared muted token so it follows the dark theme", () => {
  assert.match(css, /\.progress-hint\s*\{[^}]*color:\s*var\(--muted\)/);
});
