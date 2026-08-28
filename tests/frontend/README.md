# Frontend tests

Run with:

```
node --test tests/frontend/
```

No external dependencies are required (Node 18+'s built-in `node:test` /
`node:assert`), matching the project's own "no bundler, plain `<script>`
files" approach.

## How this works

`helpers.mjs` builds a minimal `document`/`window` stub and loads the real
`site/*.js` files into it with Node's `vm` module - the same files that ship
to production, unmodified.

## Known limitation: no real HTML parsing

The stub element's `innerHTML` is a plain string property; setting it does
**not** create real child nodes, unlike a real browser. Code that does
`el.innerHTML = "<button class=...>"` and then immediately queries for that
button via `$(".foo", el)` will get `null` in this stub, even though it
works correctly in a real browser.

This only affects the detailed row-rendering path (`renderRows` /
`toggleDetail` building each result row from a template string). Tests that
exercise the polling/status/error-handling logic around it use an empty
`results: []` payload to sidestep this gap rather than asserting on
row-level DOM the stub cannot represent. Row-level rendering (`mapApiRow`,
`toggleDetail`) is covered separately with plain unit tests that call the
mapping functions directly instead of the DOM output.

For full pixel/DOM-level confidence (in particular the original incident:
"a real browser throws `TypeError: null.textContent`, and the raw error
leaks into `#form-error`"), run the manual QA checklist in the delivered
`CHANGELOG.md` against the real deployed pages once, in an actual browser,
since it exercises the true browser HTML parser and CSS that this Node-only
harness intentionally does not attempt to reproduce.
