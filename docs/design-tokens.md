# Design tokens and theming

The viewer SPA gets its colours, type, radii and shadows from the
**hub token sheet** — one stylesheet shared by every browser app the
hub serves (landing page, this SPA, the graph pane, the coverage pane),
so they look like one product rather than four.

The sheet is owned by [`rtl_buddy`](https://github.com/rtl-buddy/rtl_buddy)
at `src/rtl_buddy/hub/theme.css` and served at `/hub/theme.css`. This
repo carries a **vendored byte-for-byte copy** at
`viewer/src/theme.css`.

## Why vendored instead of linked

The SPA ships three ways — served by the hub at `/view`, exported as a
single self-contained HTML file by `embed.py`, and run off the Vite dev
server — and only the first can reach `/hub/theme.css`. Importing the
sheet into the bundle makes all three look the same, gives `embed.py`
nothing new to inline, and sidesteps the fallback-ordering trap the hub
docs warn about (a `:root` fallback block placed *after* the `<link>`
ties on specificity, wins on document order, and pins the page light
forever).

It is the mirror image of the hub protocol schema, which *this* repo
owns and rtl_buddy vendors. Same rule in both directions: **the owner
edits, the vendor copies exact bytes, a test compares them.**

## The lockstep guard

`tests/test_vendored_theme.py` runs two independent checks:

| Test | Catches | Needs a sibling checkout |
|---|---|---|
| `test_vendored_theme_hash_is_pinned` | an edit made *here* | no — this is the one CI runs |
| `test_vendored_theme_matches_sibling_source` | upstream drift | yes; skips loudly otherwise |

Two halves because they fail for different reasons. The pinned sha256
needs nothing but the file, so it guards every CI run. The byte-compare
can only run where both repos are checked out side by side, and it
**skips with a reason** rather than failing — the sheet lands on
rtl_buddy `main` with [rtl-buddy/rtl_buddy#398](https://github.com/rtl-buddy/rtl_buddy/issues/398),
and a hard failure before then would be a red CI for a file nobody can
fix from this repo.

Updating the sheet is a two-repo change: land it in rtl_buddy, copy the
exact bytes here, re-pin `EXPECTED_SHA256` in the same commit.

## What lives where

| File | Contents |
|---|---|
| `viewer/src/theme.css` | **vendored, do not edit.** Surfaces, text tiers, accent, status + banner tints, graph column hues, coverage ramp, brand marks, type, radii, shadows. |
| `viewer/src/tokens.css` | only what the shared sheet has no home for: the seven clock pastels, the two reset strokes, and the layout constants (`--header-h`, `--sidebar-w`, `--status-h`). |
| `viewer/src/app.css` | the global rules that *consume* the tokens: document base, one disabled-control treatment, one severity map (`.rb-sev`), one backpressure ramp (`.rb-bp`). |
| `viewer/src/theme.js` | `token(name)` for code that cannot say `var(--…)`, plus the theme-change signal and the `data-theme` pin. |
| `viewer/src/palette.js` | the clock palette, the backpressure ramp and the coverage ramp, each defined once. |
| `viewer/src/severity.js` | one severity rank order and one severity → colour map. |

Import order in `main.js` is load-bearing: vendored sheet, then
`tokens.css`, then `app.css`. Component `<style>` blocks land after all
three regardless of import order.

## Light, dark, and the pin

Light is the default. Dark arrives via `@media (prefers-color-scheme:
dark)`. An app that pins a theme sets `data-theme` on `<html>`, and the
sheet's `:root[data-theme="light"|"dark"]` blocks win in **both**
directions — a light pin beats the dark media query, a dark pin beats
the `:root` defaults.

The SPA's header carries a three-state control: `system` → `light` →
`dark` → `system`. `system` writes no attribute (so the OS decides) and
the choice is remembered in `localStorage` under `rb-theme`, applied in
`main.js` *before* mount so the first paint is already correct.

`tests/tokens.spec.js` fails when the four blocks of `tokens.css`
disagree, when the CSS layout constants drift from
`src/layout/constants.js`, or when `theme.js`'s light fallbacks stop
matching what the stylesheets declare.

## Colours that CSS cannot reach

Three paths hold concrete colour strings rather than `var(--…)`:

* **Graphviz DOT.** `layout/viz.js` and `layout/blockFlow.js` bake
  colours into the DOT they generate, and viz.js bakes them again into
  the SVG. They resolve tokens at build time via `theme.js::token`, and
  `GraphCanvas` watches `themeVersion` so a theme flip triggers a fresh
  layout.
* **Overlay paint.** Overlays write inline styles onto the rendered
  SVG. They read tokens at paint time; overlay paint deliberately
  outranks both the presentation attributes Graphviz emits and the
  canvas's own themed CSS.
* **chart.js.** `AxiPerfView`'s datasets read tokens inside a computed
  that touches `themeVersion`, so a flip re-runs it and chart.js
  redraws.

`themeVersion` is bumped by a `matchMedia` listener (OS preference) and
a `MutationObserver` on `data-theme` (explicit pin), installed once by
`initTheme()`.

**Producer-supplied layout DOT is the exception.** When `view.json`
carries `graph.layout.dot`, those bytes were generated by the Python
renderer with the light palette baked in and this repo cannot rebuild
them. `GraphCanvas` re-tints the parts that would otherwise be
unreadable (text fill, edge stroke, cluster stroke) with `:deep` rules,
which outrank Graphviz's presentation attributes.

**PNG capture follows the active theme.** `capture.js` fills its
backdrop with `--bg` rather than a pinned white, because the graph's
own colours are already baked for whichever theme was active at layout
time — a dark-theme graph on a white plate is pale text on white. A
caller who wants a light plate pins light first; the canvas re-lays-out
on the flip. The SVG format is emitted as-is, with no backdrop, exactly
as before.

## Hub chrome contract

Every hub app implements the same two strips, so moving between them
costs nothing. The full contract is in rtl_buddy's
`docs/concepts/hub.md`; the SPA's implementation is:

**Top bar** (`.app-header`)

| Position | Content |
|---|---|
| left | chip logo, `rtl-buddy-view`, the design top, the rtl-buddy + SPA build chips |
| centre | Hierarchy / AXI Performance tabs, model picker |
| right | app switcher, theme control |

The switcher (`src/hubApps.js`) renders **nothing** unless the hub is
serving the bundle — off the hub, `/` and `/graph` are not ours to link
to. Under the hub it lists `⌂ hub` unconditionally (the landing page is
never a peer, so it can never evict a tab you have open) and then each
sibling app the hub advertised by injecting its data URL — the same
data-presence rule the landing cards follow.

**Bottom status strip** (`.app-status-strip`, rendered by `HubStatus`)

| Position | Content |
|---|---|
| left | connection dot + status word. One vocabulary: `connected` / `connecting…` / `offline`, on `--ok` / `--warn` / `--err`. |
| middle | peer list — every origin a user can have open (`view`, `src`, `wave`, `graph`, `cov`), connected or not. |
| right | message area on the shared severity tokens: `--err` for errors, `--warn` for warnings, `--fg-muted` for notes. |

Anything that does not fit the vocabulary — the hub's `server_version`,
why a socket dropped — lives in the element's `title`, not the status
word.

## Adding a colour

1. If the hub sheet already has a token for it, use it.
2. If it is genuinely SPA-only (a new overlay's palette), add it to
   `tokens.css` in all four blocks and to `theme.js`'s fallback table.
3. If several apps will want it, add it to the hub sheet in rtl_buddy
   instead, then re-vendor.

Never re-state a colour decision in a component's scoped `<style>`. The
drift this migration cleaned up — a triplicated backpressure ramp with
two different ambers, a clock palette computed over two different clock
sets, a severity map written out twice verbatim, four disabled-button
treatments — all started as one component "just needing that colour
here too".
