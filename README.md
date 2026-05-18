# rtl-buddy-view

RTL hierarchy and connectivity visualization tool. Pluggable parser
frontend ([Verible](https://github.com/chipsalliance/verible) for
source-faithful CST, [slang](https://github.com/MikePopoloski/slang)
via [pyslang](https://pypi.org/project/pyslang/) for elaborated views
of generates / parameterized instances) → in-memory hierarchy graph →
multiple renderers (ASCII tree, Graphviz `.dot`, Mermaid, JSON).
Designed to integrate with [rtl-buddy](https://github.com/rtl-buddy/rtl_buddy).

## Status

**Pre-alpha — scaffolding only.** Phase 1 work in progress; nothing
runs end-to-end yet. See [issue #1](https://github.com/rtl-buddy/rtl-buddy-view/issues/1)
for the Phase 1 scope and acceptance criteria.

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
  │   │             │  CST cached by file content hash
  │   └─ slang      │  pyslang elaborate, in-process (Phase 2)
  └────────┬────────┘
           ▼
  ┌─────────────────┐
  │ semantic        │  Module { name, file, line, ports,
  │ extractor       │           parameters, instances }
  └────────┬────────┘
           ▼
  ┌─────────────────┐
  │ hierarchy       │  resolves instances → tree/DAG
  │ graph builder   │  verible-first; slang fallback for
  │                 │  generates / parameterized instances
  └────────┬────────┘
           ├────► renderers (dot, tree, …)
           └────► query API (find_module, subtree, …)
```

Same shape as [rtl-buddy-cdc](https://github.com/rtl-buddy/rtl-buddy-cdc):
pure analyzer at the core, frontend is a thin wrapper, source
anchors carried through every layer.

## Quickstart

```bash
uv sync
uv run rtl-buddy-view --help    # currently prints help only; Phase 1 in progress
```

## Roadmap

- **Phase 1** (in progress): Verible frontend, semantic extractor,
  hierarchy graph, ASCII tree + Graphviz renderers, basic query API.
  Tracked in [#1](https://github.com/rtl-buddy/rtl-buddy-view/issues/1).
- **Phase 2**: slang fallback for generates / parameters, Mermaid +
  JSON renderers, `rb hier` integration in `rtl_buddy`, clock-domain
  overlay consuming rtl-buddy-cdc's domain map. Tracked in
  [#2](https://github.com/rtl-buddy/rtl-buddy-view/issues/2).
- **Phase 3**: Reset-domain overlay. Tracked in
  [#3](https://github.com/rtl-buddy/rtl-buddy-view/issues/3).

## License

BSD 3-Clause. See `LICENSE`.
