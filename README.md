# rtl-buddy-view

RTL hierarchy and connectivity visualization. Pluggable parser
frontend ([Verible](https://github.com/chipsalliance/verible) for
source-faithful CST, [slang](https://github.com/MikePopoloski/slang)
via [pyslang](https://pypi.org/project/pyslang/) for elaborated views
of generates / parameterized instances) → in-memory hierarchy graph →
four renderers (ASCII tree, Graphviz `.dot`, Mermaid, JSON). With an
optional clock-domain map from [rtl-buddy-cdc](https://github.com/rtl-buddy/rtl-buddy-cdc),
every renderer overlays clock-domain context and flags asynchronous
CDC crossings inline. Integrated into [rtl-buddy](https://github.com/rtl-buddy/rtl_buddy)
as `rb hier` — the recommended entry point for users with a
`models.yaml`-backed project.

## Why

Engineers reviewing an unfamiliar SystemVerilog design want a quick
answer to "what instantiates what, with which parameters, and how
their ports connect" — without firing up a commercial tool (Sigasi
Visualizer, DVT) or paging through RTL by hand. The open-source EDA
stack has strong synthesis (Yosys), simulation (Verilator, Icarus),
and waveform viewing (Surfer, GTKWave) but no first-class source-level
hierarchy browser.

This is **not** netlist visualization. Yosys `show` + netlistsvg
covers the gate-level case beautifully. `rtl-buddy-view` operates one
level up: on the *source* hierarchy, preserving comments, parameter
overrides, and source positions, so the rendered diagram is something
you can hand to an engineer (or an LLM) and have them navigate the
design from.

## Architecture

```
  SV sources + --top
        │
        ▼
  ┌─────────────────┐
  │   frontend      │
  │   ├─ verible    │  verible-verilog-syntax --export_json
  │   │             │  content-hashed CST cache (XDG)
  │   └─ slang      │  pyslang elaborate (Phase 2 fallback)
  └────────┬────────┘
           ▼
  ┌─────────────────┐
  │ extractor       │  Module { name, ports, params, instances,
  │ (CST → model)   │           location }  (frozen dataclasses)
  └────────┬────────┘
           ▼
  ┌─────────────────┐
  │ hierarchy graph │  build_hierarchy(table, top) → HierNode tree
  │                 │  unresolved children → blackbox leaves
  └────────┬────────┘
           │              ┌─ optional: rtl-buddy-cdc domain map
           │              │  (annotations.load_domain_map)
           ▼              ▼
  ┌─────────────────────────────────┐
  │ renderers       │ query API     │
  │ tree / dot /    │ walk, subtree,│
  │ mermaid / json  │ port_…, …     │
  └─────────────────────────────────┘
```

Same shape as [rtl-buddy-cdc](https://github.com/rtl-buddy/rtl-buddy-cdc):
pure analyzer at the core, frontend is a thin wrapper, source
anchors carried through every layer.

## Install

```bash
uv sync
# optional elaboration frontend
uv sync --extra slang
# fetch the pinned Verible binary into vendor/
uv run python scripts/fetch_verible.py
```

On macOS, `brew install verible` is also fine — the tool prefers a
PATH binary over the vendored copy.

### Building the wheel with the Vue SPA shipped

The wheel includes a pre-built copy of the Vue 3 viewer SPA so
`rtl_buddy`'s hub (`rb hub start --serve-viewer`) can discover it via
`importlib.resources` without the user passing `--viewer-bundle`. To
build a wheel locally:

```bash
uv run python scripts/prebuild_viewer.py    # npm ci + npm run build + stage
uv build --wheel                            # produces dist/*.whl
```

The prebuild step copies `viewer/dist/*` to `src/rtl_buddy_view/_viewer_bundle/`
(gitignored) and filters out source maps. The `Wheel` CI workflow
runs the same two steps on every PR touching `src/`, `viewer/`, or
`pyproject.toml`, and fails if the published wheel doesn't contain
`_viewer_bundle/index.html` + a JS asset.

Programmatic access from downstream code:

```python
from rtl_buddy_view import viewer_bundle
path = viewer_bundle.path()  # Path | None
```

Returns `None` when iterating from a checkout without a staged
bundle — callers fall back to their own placeholder.

## Use via `rb hier`

If your project already uses [rtl-buddy](https://github.com/rtl-buddy/rtl_buddy),
the recommended entry point is `rb hier <model>` — it derives `--top`
and `--filelist` from the model declared in `models.yaml`, writes the
generated filelist to `artefacts/hier/<model>/hier.f`, and forwards
every renderer flag below. All examples in the Quickstart below
translate to `rb hier <model> --format <fmt>` once the model is
registered.

The wrapper lives at
[`tools/hier_rtl_buddy_view.py`](https://github.com/rtl-buddy/rtl_buddy/blob/main/src/rtl_buddy/tools/hier_rtl_buddy_view.py)
in the rtl-buddy repo and reaches `rtl-buddy-view` via PATH. Install
this package into the same virtualenv so `rb hier` picks it up.

## Quickstart

```bash
# ASCII tree — the fastest way to eyeball a design
uv run rtl-buddy-view \
    --top counter_with_subs \
    --filelist tests/fixtures/counter_with_subs/files.f \
    --format tree

# Graphviz .dot, written to a file for piping through `dot -Tsvg`
uv run rtl-buddy-view \
    --top counter_with_subs \
    --filelist tests/fixtures/counter_with_subs/files.f \
    --format dot --output hier.dot
dot -Tsvg hier.dot -o hier.svg

# Mermaid — paste straight into a GitHub PR or README
uv run rtl-buddy-view \
    --top counter_with_subs \
    --filelist tests/fixtures/counter_with_subs/files.f \
    --format mermaid

# Machine-readable JSON (the format `rb hier` consumes)
uv run rtl-buddy-view \
    --top counter_with_subs \
    --filelist tests/fixtures/counter_with_subs/files.f \
    --format json --output hier.json
```

### Overlays — `--overlay name=path`

Every overlay (clock, reset, and the Phase 6+ coverage / physical /
waveform ones) is dispatched through a single repeatable flag:

```bash
uv run rtl-buddy-view --list-overlays
# clock    1.0
# reset    1.0

uv run rtl-buddy-view \
    --top top \
    --filelist tests/fixtures/two_clock_two_reset_design/files.f \
    --overlay clock=tests/fixtures/two_clock_two_reset_design/clock_map.json \
    --overlay reset=tests/fixtures/two_clock_two_reset_design/reset_map.json \
    --format tree
```

`--list-overlays` enumerates the registered overlay names + their
schema versions. Unknown overlay names surface the registered set
in the error message so typos self-correct. Third-party packages
register additional overlays via the
`rtl_buddy_view.overlays` entry-point group — see
[`docs/overlays.md`](docs/overlays.md) for the protocol + a
worked example.

> **Deprecation note.** The pre-Phase-4 `--cdc-annotations` and
> `--rdc-annotations` flags still work as aliases (with a
> deprecation warning each), rewriting internally to
> `--overlay clock=…` / `--overlay reset=…`. They'll be removed in
> the next major bump.

### Clock-domain overlay (consuming `--overlay clock=…`)

When rtl-buddy-cdc has produced a `--emit-domain-map` JSON for the
same design, the `clock` overlay tints subtree nodes by their
clock domain, tags per-node labels, and marks asynchronous
crossings with `⚠CDC`:

```bash
uv run rtl-buddy-view \
    --top two_clock_design \
    --filelist tests/fixtures/two_clock_design/files.f \
    --overlay clock=tests/fixtures/two_clock_design/domain_map.json \
    --format dot --clock-legend --output two_clock.dot
```

Without `--overlay clock=…` (or with an empty map), output is
byte-identical to the un-annotated case.

### Reset-domain overlay (consuming `--overlay reset=…`)

When rtl-buddy-cdc has produced a `--emit-reset-domain-map` JSON for
the same design, the `reset` overlay adds per-flop reset
binding/polarity, reset-synchroniser markers, and `⚠RDC` markers
on reset crossings. Composable with `--overlay clock=…`:

```bash
uv run rtl-buddy-view \
    --top top \
    --filelist tests/fixtures/two_clock_two_reset_design/files.f \
    --overlay clock=tests/fixtures/two_clock_two_reset_design/clock_map.json \
    --overlay reset=tests/fixtures/two_clock_two_reset_design/reset_map.json \
    --format tree
```

Output:

```
top  [clk_b]
├── u_rstgen : rstsync  [clk_b]
│   └── u_sync : ff  [clk_b]  ✓rstsync
└── u_fifo : fifo  [clk_a]
    ├── u_wr_ptr : ff  [clk_a, rst_n↓]
    └── u_rd_ptr : ff  [clk_b, rst_n↓]  ⚠RDC[rst_n:async-deassert]
```

Reset overlay surface, by renderer:

| Format | Reset binding | Synchronizer | RDC crossing |
|---|---|---|---|
| `tree` | `[<reset>↓]` (down for active-low, up for active-high) joined into the clock bracket | `✓rstsync` after the bracket | `⚠RDC[reset:kind]` suffix |
| `dot` | reset bracket line in node label | teal outline (`#0d9488`) + `✓rstsync` line | dashed-orange edge (`#ea580c`); dashed-orange arrow from top frame's reset-port anchor when applicable |
| `mermaid` | reset bracket line in node label | teal stroke + `✓rstsync` line | dashed arrow + orange stroke on destination |
| `json` | per-node `reset` object | per-node `is_reset_synchronizer: true` | per-node `reset_crossings_in[]` |

When CDC and RDC both apply to the same flop, **CDC takes
precedence on stroke / border colour** (silicon-failing
metastability is the more severe hazard), while the RDC marker rides
on the label. The other warning still surfaces — nothing is hidden.

Without `--rdc-annotations`, no reset decoration is emitted; the
output is byte-identical to the clock-only / un-annotated case.

### Coverage overlay (consuming `--overlay coverage=…`)

When rtl_buddy's Verilator coverage flow has produced LCOV data
(`rb -M cov regression --coverage-merge --coverage-coverview`), the
`coverage` overlay rolls it up per module and ships the result in
`view.json` for the web viewer, which tints each node red→green by
covered percentage (gray = no data) and shows per-node progress bars
for lines / branches / toggles plus a [Coverview](https://github.com/rtl-buddy/coverview)
deep link. The path argument accepts any of the three shapes that
flow leaves on disk:

```bash
# a Coverview-typed directory (coverage_line_*.info, coverage_branch_*.info, ...)
uv run rtl-buddy-view \
    --top counter \
    --filelist tests/fixtures/counter_with_subs/files.f \
    --overlay coverage=tests/fixtures/coverage/coverview_regression \
    --format json --output view.json

# ... or the packed archive            --overlay coverage=coverview_regression.zip
# ... or a single combined LCOV file   --overlay coverage=cov_dir/coverage_merged.info
```

Each node whose defining module has LCOV data in range gets an
`overlays.coverage` block:

```json
{
  "lines":    {"covered": 1, "total": 2, "pct": 50.0},
  "branches": {"covered": 1, "total": 2, "pct": 50.0},
  "toggles":  {"covered": 2, "total": 3, "pct": 66.7},
  "coverview_link": "http://localhost:5173/#/verif%2Fblk%2Fcounter.sv?L=5"
}
```

The join is by **source range, not instance path** — LCOV knows
files and lines, never elaborated instances — so all instances of a
module share one rollup. Channels without data are omitted; modules
with no LCOV data at all carry no block and render gray. Two knobs:
`--coverage-metric lines|branches|toggles` selects which channel
drives the viewer's heatmap tint (default `lines`), and
`--coverage-url-base URL` points the deep links at a non-default
Coverview server.

The overlay contributes to `--format json` only — tree / dot /
mermaid output stays byte-identical with or without it. Heatmap
rendering on the desktop formats and coverage diffing against a
baseline are out of scope for v1 (see #20).

To try the full loop in a browser using only in-tree fixtures:

```bash
# 1. produce a view.json the dev viewer can fetch
mkdir -p viewer/public
uv run rtl-buddy-view --top counter \
    --filelist tests/fixtures/counter_with_subs/files.f \
    --overlay coverage=tests/fixtures/coverage/coverview_regression \
    --coverage-url-base http://localhost:5174/ \
    --format json --output viewer/public/coverage_demo_view.json

# 2. serve the viewer and open it
cd viewer && npm install && npm run dev
# → http://localhost:5173/?view=coverage_demo_view.json
#   tick "coverage" in the Overlays panel for the heatmap
```

The hub chip will sit at "connecting…" under plain `npm run dev` —
that's expected (there's no `rb hub` behind the Vite origin) and
nothing coverage-related needs it. For the "Open in Coverview ↗"
deep links to land, run [Coverview](https://github.com/rtl-buddy/coverview)
at the `--coverage-url-base` address **with the matching archive
loaded** — either drop the `coverview_regression.zip` into its file
picker once per tab, or bake the data in the way deployments do
(`embed.py --inject-data <archive>.zip`, then serve `dist/`), which
makes cold deep links resolve with no interaction.

## CLI

```
rtl-buddy-view [OPTIONS]

--top, -t TEXT          Top module name. [required]
--filelist, -f PATH     One source file per line; +incdir+/-y/-f rejected.
                        [required]
--format [tree|dot|mermaid|json]
                        Output format. [default: tree]
--output, -o PATH       Write to file instead of stdout.
--frontend [verible|slang]
                        Parser frontend. [default: verible]
                        (slang activation is a Phase 2 follow-up.)
--overlay name=path     Apply the named overlay. Repeatable.
                        Built-ins: clock, reset.
                        Examples: --overlay clock=clock-map.json
                                  --overlay reset=reset-map.json
--list-overlays         List registered overlays + their schema
                        versions and exit.
--cdc-annotations PATH  (Deprecated; use --overlay clock=PATH.)
--rdc-annotations PATH  (Deprecated; use --overlay reset=PATH.)
--clock-legend          Dot-format only: emit a side legend mapping
                        clocks → palette colors. Requires a clock
                        overlay.
--version               Print `rtl-buddy-view <X.Y.Z>` and exit.
```

### `query` subcommands

A thin CLI over the [`rtl_buddy_view.query`](src/rtl_buddy_view/query.py)
Python API, so shell pipelines, agents, and non-Python tooling can ask
the same questions without a Python harness
([rtl_buddy#198](https://github.com/rtl-buddy/rtl_buddy/issues/198)).
Every subcommand takes the same design-loading options as the renderer
(`--top`, `--filelist`, `--frontend`) and prints JSON to stdout —
except `source-snippet`, whose output *is* the line-number-prefixed
citation text.

```sh
rtl-buddy-view query find-module <name>           --top T -f files.f
rtl-buddy-view query subtree <instance.path>      --top T -f files.f [--format json|tree]
rtl-buddy-view query instances-of <module-name>   --top T -f files.f
rtl-buddy-view query port-connections <inst.path> --top T -f files.f
rtl-buddy-view query source-snippet <inst.path>   --top T -f files.f [--context N] [--no-line-numbers]
```

```sh
# every instance of a module, piped through jq
rtl-buddy-view query instances-of counter_ff -t counter -f files.f \
  | jq -r '.[].instance_path'

# cite the module body of an instance, ±2 lines of context
rtl-buddy-view query source-snippet counter.u_ff -t counter -f files.f
```

Exit codes: `0` success (an empty `instances-of` list is a valid
answer), `1` lookup miss / parse failure, `2` usage errors. Node
objects reuse the view.json v1 per-node vocabulary (`instance_path`,
`module_name`, `instance_name`, `is_blackbox`, `param_overrides`,
`location`); `find-module` / `port-connections` serialize the
`extractor` dataclasses verbatim. Consumed downstream by
`rb hier-query` in [rtl_buddy](https://github.com/rtl-buddy/rtl_buddy).

### `view.json` v1 (`--format json`)

The JSON output is the locked v1 contract that the Phase 5 web
viewer (#18) and `rb hier` consume. Schema lives at
`schemas/view-v1.json`; the full field-by-field reference is in
[`docs/view-json-v1.md`](docs/view-json-v1.md). Each node carries:

- a stable `id` (full instance path), `module`, `instance_name`,
  `is_blackbox`, `parameters` dict, and `ports[]` with `expr` +
  `anchor` joined from the parent instantiation;
- a `source` block with `decl_line` / `decl_col` for the module
  declaration alongside the instance anchor;
- a `link` URI of the form
  `rtlbuddy://open?file=…&line=…&col=…` (the hub in Phase 10b is
  the URI handler);
- an `overlays` block keyed by overlay name with per-node
  metadata.

`overlays_present` at the top level lists the active overlays so a
viewer can render its panel of toggles without scanning every node.

Tree output running on a real terminal also wraps each instance
name in an OSC-8 hyperlink pointing at the same `rtlbuddy://` URI
— click-through into the hub straight from your terminal. Pipes /
redirects auto-disable OSC-8 so plain-text capture stays
machine-readable; `NO_COLOR=1` is also respected.

## Public API

A small surface is exported as a stable, SemVer-covered Python API for downstream rtl-buddy tools that need the same Verible-CST infrastructure (currently [`rtl-buddy-axi-profiler`](https://github.com/rtl-buddy/rtl-buddy-axi-profiler/issues/34) and the upcoming [`rtl-buddy-xeno`](https://github.com/rtl-buddy/rtl-buddy-xeno/issues/4); see [view#109](https://github.com/rtl-buddy/rtl-buddy-view/issues/109) for the promotion rationale). The contract:

| Symbol                                            | Purpose                                                              |
| ------------------------------------------------- | -------------------------------------------------------------------- |
| `rtl_buddy_view.frontend.verible.locate_binary`   | Locate `verible-verilog-syntax` — PATH-first, vendored fallback      |
| `rtl_buddy_view.cst_cache.get_or_compute`         | Content-hashed CST cache; takes `verible_binary`, `compute`, `cache_dir` |
| `rtl_buddy_view.cst_cache.cache_root`             | Cache-root path; takes optional `override`                           |
| `rtl_buddy_view.offsets.OffsetIndex`              | Byte-offset → `(line, column)`, UTF-8-correct, O(log n)              |
| `rtl_buddy_view.extractor.SourceLocation`         | Frozen `(file, start_line, start_column, end_line, end_column)`      |

Plus the existing `extractor` dataclasses (`Module`, `Port`, `Parameter`, `Instance`) consumed via the JSON renderer's pinned contract.

Anything **not** in that list (graph internals, renderer internals, the SPA bundle path, the underscore-prefixed modules) is implementation detail and may change without notice. The legacy underscore-prefixed imports (`rtl_buddy_view._cst_cache`, `rtl_buddy_view._offsets`) remain available as deprecation shims for one minor version with a `DeprecationWarning` pointing at the new locations.

### CST cache — shared across rtl-buddy tools

The cache directory is a **shared resource** across any rtl-buddy tool that imports `cst_cache`. Resolution order, decreasing precedence:

1. `cache_dir` keyword argument to `get_or_compute` (caller-injected; the rtl_buddy orchestrator's preferred path — it reads `cfg-cst-cache.dir` from `root_config.yaml` and passes it through).
2. `RTL_BUDDY_CACHE_DIR` environment variable.
3. `RTL_BUDDY_VIEW_CACHE_DIR` (deprecated alias; emits a `DeprecationWarning`).
4. `$XDG_CACHE_HOME/rtl-buddy/sv-cst/` (per the XDG Base Directory spec).
5. `~/.cache/rtl-buddy/sv-cst/` (XDG default).

If only the legacy `<xdg-cache>/rtl-buddy-view/cst/` directory exists on disk, it is read as a one-time fallback with a `DeprecationWarning`. New writes always go to the new path.

`RTL_BUDDY_NO_CACHE=1` disables caching entirely (useful for CI and Verible-overhead measurement); `RTL_BUDDY_VIEW_NO_CACHE` is honoured as a deprecated alias.

## Versioning & compatibility

Two contracts are versioned independently:

- **The `view.json` payload** is versioned by its `schema_version` field (`schemas/view-v1.json`). `1.x` is backward-compatible: minor bumps only *add* fields, never rename or retype existing ones, so any `1.x` consumer can rely on field presence + type per the schema.
- **The Python API** in [Public API](#public-api) is treated as stable and follows SemVer once the package reaches `1.0`.

While `rtl-buddy-view` is pre-`1.0`, downstream consumers (rtl_buddy, the axi-profiler, xeno) should **pin a floor and guard at runtime rather than cap the upper bound**:

```toml
# good — floor only, no speculative upper cap
rtl-buddy-view >= 0.2.1
```

`0.2.1` is the first PyPI release that clears the `0.2.0` filelist `IsADirectoryError` and ships the compiled SPA. An upper cap like `< 0.3.0` would be a *guess* at where a break lands; pin the known-good floor instead and let the consumer enforce it at runtime by probing `rtl-buddy-view --version` (or `importlib.metadata.version("rtl-buddy-view")`) and raising a clear upgrade error when it sees something older than its floor. Once this package cuts `1.0`, the SemVer-safe range becomes the conventional `>=1,<2`.

The CLI exposes the probe directly:

```console
$ rtl-buddy-view --version
rtl-buddy-view 0.2.1
```

Consumers extract the version with `r"rtl-buddy-view\s+(\d+\.\d+(?:\.\d+)?)"` (the optional third component + the trailing `.devN+g<sha>` tolerate untagged/shallow builds); the same string is available in-process as `rtl_buddy_view.__version__`.

## Roadmap

- **Phase 1** ✅ — Verible frontend, semantic extractor, hierarchy
  graph, ASCII tree + Graphviz renderers, query API.
  ([#1](https://github.com/rtl-buddy/rtl-buddy-view/issues/1))
- **Phase 2** ✅ — Mermaid + JSON renderers, clock-domain overlay
  consuming rtl-buddy-cdc's schema-v1.0 domain map, deterministic
  output across all formats, JSON contract pinned for downstream
  `rb hier`. ([#2](https://github.com/rtl-buddy/rtl-buddy-view/issues/2))
- **Phase 3** ✅ — Reset-domain overlay consuming rtl-buddy-cdc's
  schema-v1.0 reset-domain map: per-flop reset binding + polarity,
  reset-synchroniser markers, dashed-orange RDC edges, and
  combined-overlay rendering across all four output formats.
  ([#3](https://github.com/rtl-buddy/rtl-buddy-view/issues/3))
- **Phase 4** — Generalised overlay plugin layer
  (`--overlay name=path`), locked `view.json` v1 schema, OSC-8
  hyperlinks in the tree renderer.
  ([#17](https://github.com/rtl-buddy/rtl-buddy-view/issues/17))

## License

BSD 3-Clause. See `LICENSE`.
