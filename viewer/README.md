# rtl-buddy-view viewer

Browser-based interactive viewer for `view.json` v1 — the JSON
contract the Python `rtl-buddy-view --format json` renderer emits.

Vue 3 + Vite SPA. Graphviz layout runs in the browser via
[viz.js](https://github.com/mdaines/viz-js) (Graphviz compiled to
WASM), so the viewer needs no server runtime and can be distributed
as a single self-contained HTML file via `embed.py`.

## Quickstart

```bash
cd viewer/
npm install
npm run dev
```

Open the printed URL. Drag a `view.json` onto the page, pick it
through the file dialog, or pass `?view=URL` in the URL.

To regenerate a fixture's `view.json` from the repo root:

```bash
uv run rtl-buddy-view \
    --top top \
    --filelist tests/fixtures/two_clock_two_reset_design/files.f \
    --overlay clock=tests/fixtures/two_clock_two_reset_design/clock_map.json \
    --overlay reset=tests/fixtures/two_clock_two_reset_design/reset_map.json \
    --format json \
    --output /tmp/view.json
```

Then drop `/tmp/view.json` onto the running viewer.

## Standalone HTML bundle

```bash
npm run build
python embed.py --inject-data PATH/TO/view.json --output viewer.html
open viewer.html  # works offline; no further fetches.
```

`embed.py` inlines every same-origin asset (JS bundles, CSS, viz.js
WASM, the optional `view.json` payload) into one HTML file. Stdlib
only — no `pip` / `uv` step needed.

## Tests

```bash
npm test        # vitest unit suite — parser, store, layout, overlays
```

## Architecture

```
src/
├── main.js             # Vue + Pinia bootstrap
├── App.vue             # state-machine shell (idle / loading / ready / error)
├── store.js            # Pinia store — graph, overlays, selection, hub state
├── parse.js            # view.json v1 loader + validator
├── layout/
│   └── viz.js          # @viz-js/viz wrapper + DOT generation
├── components/
│   ├── GraphCanvas.vue # SVG canvas, pan/zoom, click-to-open
│   ├── OverlayPanel.vue# per-overlay toggles + legend
│   ├── NodeDetail.vue  # selected-node ports/params/overlay values
│   └── HubStatus.vue   # Phase 5 stub indicator (Phase 10d wires it)
├── overlays/           # one module per overlay name (mirrors Python's overlays/)
│   ├── clock.js
│   ├── reset.js
│   └── index.js        # registry: name → overlay module
└── composables/
    └── useHub.js       # Phase 5 stub; Phase 10d swaps in real WebSocket
```

The viewer treats `view.json` as the cross-process contract;
overlay rendering is one JS module per overlay name. An overlay
in `graph.overlays_present` that the viewer doesn't know about
shows up tagged `unknown` in the sidebar and contributes nothing
to the SVG — never an error. New overlays ship as a single
`src/overlays/<name>.js` file alongside their Python emit code in
later phases.

## Hub integration

`HubStatus.vue` shows `disconnected` and `useHub()` is a no-op
stub. Phase 10d (`rtl-buddy-view/issues/TBD`) replaces the
composable with a real WebSocket connection; the components stay
unchanged.
