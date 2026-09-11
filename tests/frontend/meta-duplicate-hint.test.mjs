import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { buildSandbox, loadCommon, SITE_DIR } from "./helpers.mjs";

// Load the real meta.js into the shared stub sandbox and read its
// module-level helpers back out - the same approach map-api-row.test.mjs
// uses for mapApiRow. duplicateHint is a plain function of `count`, so it
// can be unit-tested directly without the DOM-heavy toggleDetail path
// (which the Node stub can't render - see tests/frontend/README.md).
function loadMeta() {
  const sandbox = buildSandbox();
  const HI = loadCommon(sandbox);
  const code = fs.readFileSync(path.join(SITE_DIR, "meta.js"), "utf8");
  vm.runInContext(code, sandbox, { filename: "meta.js" });
  return { duplicateHint: sandbox.duplicateHint, pluralRu: HI.pluralRu };
}

// The noun forms Meta uses for duplicate <title>/meta tags.
const TAG_FORMS = ["тег", "тега", "тегов"];

// Declension across the full set of characteristic values, including the
// tricky teens (11) and the >20 wrap-around (21 -> "тег", 22 -> "тега").
const EXPECTED_FORM = {
  1: "тег",
  2: "тега",
  5: "тегов",
  8: "тегов",
  11: "тегов",
  21: "тег",
  22: "тега",
};

test("Meta declines 'тег' correctly for 1/2/5/8/11/21/22", () => {
  const { pluralRu } = loadMeta();
  for (const [nStr, form] of Object.entries(EXPECTED_FORM)) {
    const n = Number(nStr);
    assert.equal(pluralRu(n, ...TAG_FORMS), form, `pluralRu(${n})`);
  }
});

test("duplicateHint builds the warning only for count > 1, with correct plural", () => {
  const { duplicateHint } = loadMeta();

  // No duplicate warning for a single tag or an absent one.
  assert.equal(duplicateHint(0), null);
  assert.equal(duplicateHint(1), null);

  const cases = {
    2: "2 тега — проверьте дубли в HTML",
    5: "5 тегов — проверьте дубли в HTML",
    8: "8 тегов — проверьте дубли в HTML",
    11: "11 тегов — проверьте дубли в HTML",
    21: "21 тег — проверьте дубли в HTML",
    22: "22 тега — проверьте дубли в HTML",
  };
  for (const [nStr, text] of Object.entries(cases)) {
    const hint = duplicateHint(Number(nStr));
    assert.equal(hint.text, text, `duplicateHint(${nStr}).text`);
    assert.equal(hint.state, "warn", `duplicateHint(${nStr}).state`);
  }
});
