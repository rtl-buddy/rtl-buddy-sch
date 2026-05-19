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

### Clock-domain overlay

When rtl-buddy-cdc has produced a `--emit-domain-map` JSON for the
same design, pass it via `--cdc-annotations`. Every renderer picks up
clock coloring, per-node clock tags, and `⚠CDC` markers on the
asynchronous crossings:

```bash
uv run rtl-buddy-view \
    --top two_clock_design \
    --filelist tests/fixtures/two_clock_design/files.f \
    --cdc-annotations tests/fixtures/two_clock_design/domain_map.json \
    --format dot --clock-legend --output two_clock.dot
```

Without `--cdc-annotations` (or with an empty map), output is
byte-identical to the un-annotated case.

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
--cdc-annotations PATH  Optional clock-domain map JSON from
                        `rtl-buddy-cdc --emit-domain-map`.
--clock-legend          Dot-format only: emit a side legend mapping
                        clocks → palette colors. Requires
                        --cdc-annotations.
```

## Roadmap

- **Phase 1** ✅ — Verible frontend, semantic extractor, hierarchy
  graph, ASCII tree + Graphviz renderers, query API.
  ([#1](https://github.com/rtl-buddy/rtl-buddy-view/issues/1))
- **Phase 2** ✅ — Mermaid + JSON renderers, clock-domain overlay
  consuming rtl-buddy-cdc's schema-v1.0 domain map, deterministic
  output across all formats, JSON contract pinned for downstream
  `rb hier`. ([#2](https://github.com/rtl-buddy/rtl-buddy-view/issues/2))
- **Phase 3** — Reset-domain overlay (blocked on rtl-buddy-cdc#107 /
  #108 producer-side work).
  ([#3](https://github.com/rtl-buddy/rtl-buddy-view/issues/3))

## License

BSD 3-Clause. See `LICENSE`.
