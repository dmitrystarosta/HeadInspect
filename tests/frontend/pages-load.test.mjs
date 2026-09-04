import test from "node:test";
import assert from "node:assert/strict";
import { buildSandbox, loadCommon, loadModuleScript } from "./helpers.mjs";

// Item 14 / P1-5: every module script must load cleanly alongside common.js,
// with no unhandled top-level exception, regardless of which page it is
// on. This does not fully simulate a live job (see open-existing-job.test
// for that), but it is exactly the class of "fixed in 3 of 4 files" /
// stale-copy bug the independent audit flagged (P3-1, P1-5): if a page's
// script relies on something common.js does not actually provide, this
// test fails immediately for that one file.
const MODULES = ["home.js", "app.js", "meta.js", "schema.js", "sitemap.js", "canonical.js"];

for (const filename of MODULES) {
  test(`${filename} loads without throwing alongside common.js`, () => {
    const sandbox = buildSandbox();
    loadCommon(sandbox);
    assert.doesNotThrow(() => loadModuleScript(sandbox, filename));
  });
}
