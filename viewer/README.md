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
npm test          # vitest unit suite — parser, store, layout, overlays, hub
npm run test:e2e  # Playwright — snapshot + hub-wiring suites
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
│   ├── GraphCanvas.vue       # SVG canvas, pan/zoom, click-to-open
│   ├── OverlayPanel.vue      # per-overlay toggles + legend
│   ├── NodeDetail.vue        # selected-node ports/params/overlay values
│   ├── HubStatus.vue         # connection indicator + peer/error popover
│   ├── ToastHost.vue         # surfaces hub error envelopes
│   └── DiagnosticsPanel.vue  # findings from diagnostics_set (per source)
├── overlays/           # one module per overlay name (mirrors Python's overlays/)
│   ├── clock.js
│   ├── reset.js
│   └── index.js        # registry: name → overlay module
└── composables/
    └── useHub.js             # WebSocket client for rtl-buddy-hub (/ws)
```

The viewer treats `view.json` as the cross-process contract;
overlay rendering is one JS module per overlay name. An overlay
in `graph.overlays_present` that the viewer doesn't know about
shows up tagged `unknown` in the sidebar and contributes nothing
to the SVG — never an error. New overlays ship as a single
`src/overlays/<name>.js` file alongside their Python emit code in
later phases.

## Hub integration (Phase 10d, [#23](https://github.com/rtl-buddy/rtl-buddy-view/issues/23))

The viewer is a WebSocket client of [`rtl-buddy-hub`](https://github.com/rtl-buddy/rtl_buddy)
when served by the hub itself. Quickstart:

```bash
# In an RTL project that has been hier'd into a view.json:
uv run rb hub start --serve-viewer --viewer-bundle path/to/viewer/dist/
# then open http://127.0.0.1:<http_port>/ — the URL is also printed
# by `rb hub status`.
```

The hub serves the SPA at `/` and exposes its protocol over
`/ws` on the same origin. The page is preamble-injected with
`window.__RTL_BUDDY_HUB__ = "<host>:<port>"`; the SPA never has
to discover the address itself.

### Live behaviour

| Action | Wire effect |
|---|---|
| Left-click a node | `selection_changed{instance_path, origin: "view"}` — hub routes to surfer and nvim. |
| Right-click a node | Opens the node's `link` URI (`rtlbuddy://…` / `vscode://…`) directly, bypassing the hub. |
| `cursor_time_changed` from surfer | Stashed on the store; future wave-overlay snapshots can re-tint. |
| `selection_changed` from another origin | Selects the matching node (NodeDetail repaints). |
| `diagnostics_set` from any producer | Renders in the sidebar `DiagnosticsPanel`, grouped by `source`. Empty `items` clears the source. Hub replays cached sources on welcome. |
| `error` envelope | Surfaces as a toast (one at a time, auto-dismissed). |

### Offline fallback

When no hub is reachable the `HubStatus` pill stays `disconnected` and
left-clicks fall back to the right-click behaviour — `node.link`
dispatched to the OS. Phase 5 (offline) ergonomics are preserved.

### Protocol

Envelope shape, error codes, and message types are pinned by
[`hub-protocol-v1.json`](https://github.com/rtl-buddy/rtl_buddy/blob/main/src/rtl_buddy/hub/schema/hub-protocol-v1.json)
in `rtl_buddy`. The viewer registers as `origin: "view"`; the hub
suppresses echo-back to the originating origin class, so a viewer
click never bounces back as a redundant `selection_changed`.
