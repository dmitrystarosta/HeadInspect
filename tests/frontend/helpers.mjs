// Minimal DOM/window stub sufficient to load HeadInspect's plain-script
// frontend files under Node's built-in test runner, with no external
// dependencies (no jsdom, no bundler) - matching the project's own
// "no build step" static site.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const SITE_DIR = path.join(__dirname, "..", "..", "site");

function matchesSimpleSelector(el, selector) {
  // Supports what this test suite needs: a single ".classname", or a
  // two-token descendant combinator ".classname tagname" (e.g.
  // ".audit-module-body p") - not a general CSS engine.
  const classOnly = /^\.([\w-]+)$/.exec(selector.trim());
  if (classOnly) {
    return !!(el.classList && el.classList.contains(classOnly[1]));
  }
  const descendant = /^\.([\w-]+)\s+(\w+)$/.exec(selector.trim());
  if (descendant) {
    const [, ancestorClass, tag] = descendant;
    if ((el.tagName || "").toLowerCase() !== tag.toLowerCase()) return false;
    let node = el._parent;
    while (node) {
      if (node.classList && node.classList.contains(ancestorClass)) return true;
      node = node._parent;
    }
    return false;
  }
  return false;
}

function queryAllDescendants(root, selector, results) {
  for (const child of root.children || []) {
    if (matchesSimpleSelector(child, selector)) results.push(child);
    queryAllDescendants(child, selector, results);
  }
  return results;
}

export function makeEl(tag) {
  const el = {
    tagName: (tag || "div").toUpperCase(),
    children: [],
    _parent: null,
    get parentElement() { return this._parent; },
    classList: {
      _set: new Set(),
      add(...c) { c.forEach(x => this._set.add(x)); },
      remove(...c) { c.forEach(x => this._set.delete(x)); },
      toggle(c, force) {
        if (force === undefined) {
          if (this._set.has(c)) { this._set.delete(c); return false; }
          this._set.add(c);
          return true;
        }
        if (force) this._set.add(c); else this._set.delete(c);
        return force;
      },
      contains(c) { return this._set.has(c); }
    },
    dataset: {},
    style: {},
    attributes: {},
    _text: "",
    hidden: false,
    disabled: false,
    get className() { return [...this.classList._set].join(" "); },
    set className(v) {
      this.classList._set.clear();
      String(v).split(/\s+/).filter(Boolean).forEach(c => this.classList._set.add(c));
    },
    get textContent() { return this._text; },
    set textContent(v) { this._text = v; },
    get innerHTML() { return this._html || ""; },
    set innerHTML(v) { this._html = v; },
    setAttribute(k, v) { this.attributes[k] = v; },
    getAttribute(k) { return this.attributes[k] !== undefined ? this.attributes[k] : null; },
    addEventListener() {},
    appendChild(c) { c._parent = this; this.children.push(c); return c; },
    prepend(c) { c._parent = this; this.children.unshift(c); return c; },
    replaceChildren() {},
    cloneNode() { return makeEl(tag); },
    closest() { return null; },
    scrollIntoView() {},
    querySelector(sel) { return queryAllDescendants(this, sel, [])[0] || null; },
    querySelectorAll(sel) { return queryAllDescendants(this, sel, []); },
    remove() {
      if (this._parent) {
        const idx = this._parent.children.indexOf(this);
        if (idx !== -1) this._parent.children.splice(idx, 1);
        this._parent = null;
      }
    },
  };
  return el;
}

// A fake document that resolves a fixed set of selectors (typically "#id")
// to pre-built elements, so a full page's worth of DOM lookups succeeds
// without needing a real browser or jsdom. Any selector not in the registry
// still safely resolves to null/[] like the stub in buildSandbox.
export function buildRegistryDocument(idRegistry, moduleRegistry = {}) {
  const byId = new Map(Object.entries(idRegistry));
  const byModule = new Map(Object.entries(moduleRegistry));
  return {
    querySelector(sel) {
      const idMatch = /^#([\w-]+)$/.exec(sel);
      if (idMatch && byId.has(idMatch[1])) return byId.get(idMatch[1]);
      // Supports home.js's `.audit-module[data-module="open-graph"]` lookup
      // specifically - the only compound selector this test suite needs.
      const moduleMatch = /^\.audit-module\[data-module="([\w-]+)"\]$/.exec(sel);
      if (moduleMatch && byModule.has(moduleMatch[1])) return byModule.get(moduleMatch[1]);
      return null;
    },
    querySelectorAll() { return []; },
    createElement(tag) { return makeEl(tag); },
    addEventListener() {},
  };
}

export function buildSandbox({ fetchImpl, documentImpl } = {}) {
  const fakeDocument = documentImpl || {
    querySelector() { return null; },
    querySelectorAll() { return []; },
    createElement(tag) { return makeEl(tag); },
    addEventListener() {},
  };

  const sandbox = {};
  sandbox.document = fakeDocument;
  sandbox.URL = URL;
  sandbox.URLSearchParams = URLSearchParams;
  sandbox.AbortController = AbortController;
  sandbox.console = console;
  sandbox.fetch = fetchImpl || (async () => { throw new Error("no network in test"); });
  sandbox.setTimeout = setTimeout;
  sandbox.alert = () => {};
  sandbox.location = { search: "", pathname: "/", origin: "https://headinspect.ru" };
  sandbox.history = { replaceState() {} };
  sandbox.scrollTo = () => {};
  sandbox.window = sandbox; // in real browsers window === the global object
  vm.createContext(sandbox);
  return sandbox;
}

export function loadCommon(sandbox) {
  const code = fs.readFileSync(path.join(SITE_DIR, "common.js"), "utf8");
  vm.runInContext(code, sandbox, { filename: "common.js" });
  return sandbox.HI;
}

export function loadModuleScript(sandbox, filename) {
  const code = fs.readFileSync(path.join(SITE_DIR, filename), "utf8");
  vm.runInContext(code, sandbox, { filename });
}
