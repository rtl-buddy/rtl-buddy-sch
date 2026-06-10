# `view.json` v1 reference

`view.json` is the locked machine-readable output of
rtl-buddy-view's `--format json`. The Phase 5 interactive viewer
(rtl-buddy-view#18), the `rb hier` consumer in rtl-buddy, and any
third-party tool that wants to integrate with the rtl-buddy stack
parse it.

The authoritative JSON Schema is
[`schemas/view-v1.json`](../schemas/view-v1.json); this document is
the human-readable companion. Both ship in lockstep; the fixture
test
[`tests/test_view_json_schema_fixtures.py`](../tests/test_view_json_schema_fixtures.py)
validates every shipped fixture against the schema on every PR.

> Phase 4 (rtl-buddy-view#17) locked the schema at v1. Bumping to
> v2 is allowed in a future major; v1.x payloads are backward-
> compatible by contract (see § 9). v1.1 (rtl-buddy-view#99) added
> the `dut_top` and `tb_top` descriptive fields for the testbench-
> top view; v1.0 consumers that ignore unknown keys parse v1.1
> payloads unchanged.

## 1. Top-level envelope

```jsonc
{
  "schema_version": "1.1",
  "top": "tb_top",
  "dut_top": "soc_top",
  "tb_top": "tb_top",
  "tool": {
    "name": "rtl-buddy-view",
    "version": "0.1.0"
  },
  "nodes": [ /* ... */ ],
  "edges": [ /* ... */ ],
  "overlays_present": ["clock", "reset"]
}
```

| Field              | Type                | Notes |
| ------------------ | ------------------- | ----- |
| `schema_version`   | string              | `MAJOR.MINOR`. Currently `"1.1"`. |
| `top`              | string              | Rendered root's module name. Equals `dut_top` when only `--top` was passed; equals `tb_top` when `--tb-top` was passed (with or without `--top`). |
| `dut_top`          | string \| null      | (v1.1) Module name supplied via `--top`. Non-null whenever `--top` was passed; null when only `--tb-top` was passed. SPA derives DUT instance anchors by filtering `nodes[]` for `module == dut_top` (no separate `dut_anchors` list — derivable, not stored). |
| `tb_top`           | string \| null      | (v1.1) Module name supplied via `--tb-top`. Non-null whenever `--tb-top` was passed; null when only `--top` was passed. When non-null, the rendered root is the testbench top — the hub's wave path is the identity of the view path (no `tb_prefix` strip). |
| `tool`             | object (optional)   | `{name, version}` — producer self-identification. Consumers shouldn't gate on this. |
| `nodes`            | array of `node`     | Every elaborated instance in the design, sorted by `id`. |
| `edges`            | array of `edge`     | Parent → child instantiation edges, sorted by `(from, to)`. |
| `overlays_present` | array of string     | Sorted overlay names whose annotations contributed. The viewer dispatches per-overlay rendering off this list. |

Additional properties are allowed at the top level; consumers that
don't recognise them should pass them through.

### 1.1 CLI modes that drive the envelope

`--top` and `--tb-top` are **independent** flags on `rtl-buddy-view`;
at least one is required.

| CLI                                 | `top`     | `dut_top` | `tb_top` | Notes |
| ----------------------------------- | --------- | --------- | -------- | ----- |
| `--top X`                           | `X`       | `X`       | `null`   | DUT view (byte-identical to v1.0 behaviour). |
| `--tb-top Y`                        | `Y`       | `null`    | `Y`      | TB view, no DUT marking. Overlays (CDC, AXI perf) still anchor under their own `design_top` by walking `nodes[]` at load time. |
| `--top X --tb-top Y`                | `Y`       | `X`       | `Y`      | TB view with DUT recorded. SPA draws a dashed boundary at every instance whose module is `X`. |
| _(neither)_                         | —         | —         | —        | Error: exit code 2. |

The renderer does **not** error when `--top` and `--tb-top` are
both set but the DUT module doesn't appear in the TB elaboration.
That's surfaced at overlay load time, where the overlay's
`design_top` fails to resolve into any instance path — a load-time
warning is more actionable than a render-time error.

## 2. Node objects

```jsonc
{
  "id": "soc_top.u_cpu.u_alu",
  "module": "alu",
  "instance_name": "u_alu",
  "is_blackbox": false,
  "source": {
    "file": "/abs/path/alu.sv",
    "start_line": 12,
    "start_column": 1,
    "end_line": 348,
    "end_column": 9,
    "decl_line": 12,
    "decl_col": 8
  },
  "ports": [
    {
      "name": "clk",
      "dir": "input",
      "expr": "core_clk",
      "anchor": { "line": 14, "col": 24 }
    }
  ],
  "parameters": { "WIDTH": "32" },
  "link": "rtlbuddy://open?file=%2Fabs%2Fpath%2Falu.sv&line=12&col=8",
  "overlays": {
    "clock": { "clock": "core_clk", "group": "g0" },
    "reset": { "reset": "rst_n", "polarity": "low" }
  }
}
```

| Field           | Type              | Notes |
| --------------- | ----------------- | ----- |
| `id`            | string            | **Stable instance path**, dot-separated from `top`. Reproducible across runs given the same RTL. |
| `module`        | string            | Module-type name this instance refers to. |
| `instance_name` | string \| null    | Source-level instance name. `null` for the top node. |
| `is_blackbox`   | boolean           | `true` when the module wasn't in the filelist — viewers render with a dashed border. |
| `source`        | `source_block` \| null | Anchor for this node. `null` only for synthetic / blackbox nodes the extractor never saw. |
| `ports`         | array of `port`   | Per-port bindings at THIS instance (`expr` is what the parent connected). |
| `parameters`    | object            | Override → verbatim source slice (`"32"`, `"\"hello\""`, `` "`MAX_WIDTH" ``). Empty when none. |
| `link`          | string \| null    | `rtlbuddy://` URI (§4). `null` only when no source location is known. |
| `overlays`      | object            | Per-overlay metadata, keyed by overlay name. Missing keys mean no contribution; never an error. |

### `source_block`

```jsonc
{
  "file": "/abs/path/alu.sv",
  "start_line": 12,
  "start_column": 1,
  "end_line": 348,
  "end_column": 9,
  "decl_line": 12,
  "decl_col": 8
}
```

Only `file` is required; every other field can be `null`. The
declaration anchor (`decl_line`, `decl_col`) is what the `link`
URI references — that's the cursor target editors jump to.

### `port`

```jsonc
{
  "name": "clk",
  "dir": "input",
  "expr": "core_clk",
  "anchor": { "line": 14, "col": 24 }
}
```

- `name` (string, required) — declared port name on the child module.
- `dir` (`"input" | "output" | "inout" | null`) — direction.
- `expr` (string | null) — net expression bound at instantiation;
  `null` when the port is declared but unconnected.
- `anchor` (object | null) — `{line, col}` for the binding site
  in the parent module.
- `port_kind` (`"wire" | "interface" | "interface_signal"`, optional)
  — discriminator for the port's source-level shape:
  - omitted (default `wire`) on scalar / vector signals with a
    `dir`.
  - `"interface"` on SystemVerilog interface ports
    (`test_mem_if.sub m`) whose interface body the producer
    couldn't resolve. The port carries no `dir`; the SPA renders
    it as an amber-tinted italic `▶▶` row and the wave overlay
    skips it.
  - `"interface_signal"` on the per-signal *flattening* of an
    interface port (e.g. `m.req`, `m.addr`). Carries a real `dir`
    pinned by the source modport (`modport sub(input a, b)` →
    `dir: "input"`); behaves like a normal wire for connectivity
    and wave-overlay purposes.
- `interface_type` (string | null, optional) — interface name (e.g.
  `"test_mem_if"`). Present only with `port_kind` of `"interface"`
  or `"interface_signal"`.
- `modport` (string | null, optional) — modport name (e.g. `"sub"`).
  May be `null` when the source declared a bare interface port
  without a modport. Present only with `port_kind` of `"interface"`
  or `"interface_signal"`.

Bundle interface ports (`port_kind == "interface"`) are
not value-badge-eligible (one literal next to a bundle would be
ambiguous). Flattened signals (`port_kind == "interface_signal"`)
are scalar and behave like wires.

Positional port connections on blackbox instances are intentionally
*excluded* from `ports[]` (they have no port name to bind to);
they still appear in the edge's `port_pairs[]` so the full
connection sequence is recoverable.

## 3. Edge objects

```jsonc
{
  "from": "soc_top.u_cpu",
  "to": "soc_top.u_cpu.u_alu",
  "port_pairs": [
    ["alu_out", "out"],
    ["alu_in",  "in"]
  ],
  "overlays": {
    "clock": { "crossing": false }
  }
}
```

| Field        | Type                            | Notes |
| ------------ | ------------------------------- | ----- |
| `from`       | string                          | Parent `node.id`. |
| `to`         | string                          | Child `node.id`. |
| `port_pairs` | array of `[expr, child_port]`   | Tuples of `[net_expr_at_parent, child_port_name]`. The wave→view joiner (Phase 9) traces signals across hierarchy levels through these. Either element can be `null` (positional connection, dangling port). |
| `overlays`   | object                          | Per-overlay edge metadata. `clock.crossing: true` marks a CDC edge; `reset.crossing: true` an RDC edge. |

## 4. The `rtlbuddy://` URI

```
rtlbuddy://open?file=<urlencoded-abs-path>&line=<n>&col=<n>
```

- **Scheme**: `rtlbuddy`
- **Authority/host**: `open` — the only verb today; reserved space
  for future verbs like `select`, `highlight`.
- **Query parameters**:
  - `file` (required) — absolute path, URL-encoded.
  - `line` (required) — 1-based line.
  - `col` (optional) — 1-based column.

The hub (Phase 10b, `rtl-buddy/rtl_buddy#115`) is the URI handler:
when an editor clicks the link, the hub translates it to a
`select.src` event and broadcasts it to all connected clients
(see [`hub-protocol.md`](hub-protocol.md)). Outside the hub
context, the URI is informational — the OSC-8 tree wraps each
instance name in this URI so terminal click-through works
naturally.

URIs are emitted with `urllib.parse.quote` over the file path so
spaces and unicode survive copy-paste.

## 5. Determinism

`view.json` is byte-identical across runs over the same RTL:

- `nodes[]` is sorted by `id`.
- `edges[]` is sorted by `(from, to)`.
- `overlays_present[]` is sorted alphabetically.
- Within each `node.overlays.<name>` object, the producer (overlay
  module) is responsible for stable ordering of its own keys.
- `parameters` is a JSON object; order is preserved insertion
  order from the source.

Golden tests downstream rely on this — don't introduce hash-order
iteration into emitted bytes.

## 6. `overlays_present` and per-node `overlays`

Two complementary views of overlay state:

- `overlays_present` (top-level) — *which* overlays contributed to
  this payload. A viewer reads this to decide which JS modules to
  load and which controls to render.
- `node.overlays.<name>` / `edge.overlays.<name>` — *what* each
  overlay contributed for *this* node/edge.

A missing `node.overlays.coverage` doesn't mean "coverage is off";
it means "the coverage overlay had no metadata for this specific
node" (e.g. an uncovered subtree, a blackbox). Distinguish from
"coverage overlay not loaded" via `overlays_present`.

### Shape of `node.overlays.<name>` per built-in

The built-ins document their per-node payload in their own source.
A flavour:

```jsonc
"clock":  { "clock": "core_clk", "group": "g0" }
"reset":  { "reset": "rst_n", "polarity": "low",
            "synchroniser": false }
"coverage": { "lines":    { "covered": 124, "total": 138, "pct": 89.9 },
              "branches": { "covered": 22,  "total": 30,  "pct": 73.3 },
              "toggles":  { "covered": 78,  "total": 96,  "pct": 81.3 },
              "coverview_link": "http://localhost:5173/#/blk%2Ffifo.sv?L=42" }
```

The `coverage` block (Phase 6, #20) is joined by the defining
module's source range, so all instances of one module share a
rollup; channels with no LCOV units in range are omitted, and a
node with no data at all carries no block. The companion
`overlay_meta.coverage` envelope block records `source`,
`url_base`, and the `metric` channel the viewer's heatmap tint
should use.

Third-party overlays own their own per-node and per-edge key
shapes — keep them stable across `schema_version` minors.

## 7. JSON Schema validation

[`schemas/view-v1.json`](../schemas/view-v1.json) is a Draft
2020-12 schema that any payload must satisfy. The producer
validates outputs against it implicitly via the fixture test
suite, so a broken renderer surfaces at PR time. Consumers
should validate too — `jsonschema>=4.21` is a runtime dependency
of rtl-buddy-view itself for exactly this reason.

```python
import json
import jsonschema

schema = json.loads(Path("schemas/view-v1.json").read_text())
payload = json.loads(Path("view.json").read_text())
jsonschema.validate(payload, schema)
```

Major-version mismatches are a *consumer* concern — gate on
`payload["schema_version"].split(".")[0] == "1"` and fail loudly
if it isn't.

## 8. Stability guarantees

The following are pinned for the lifetime of v1.x and may not
change without a major bump:

1. **Stable IDs.** `node.id` is the elaborated instance path, dot-
   separated from `top`. Two runs over unchanged RTL produce
   identical IDs.
2. **Source anchors on every node** (except `is_blackbox` nodes
   the extractor never saw): absolute `file` path plus line/column
   ranges. Required by coverage range-aggregation (Phase 6) and
   editor handoff (Phase 10).
3. **`link` URI on every node** with a known source location.
   Scheme is `rtlbuddy://`, host is `open`, query carries `file`,
   `line`, optional `col`.
4. **Per-overlay metadata under `overlays`** keyed by overlay
   `name`. Missing keys are not errors.

Adding keys at any level is *not* a breaking change. Removing or
retyping any of the above *is*.

## 9. Versioning policy

- **Minor bump** (`1.0` → `1.1`): purely additive. New optional
  fields, new overlays, new metadata keys under `overlays`. v1.0
  consumers parse v1.1 payloads correctly by ignoring unknown
  keys. The v1.1 bump (issue #99) added envelope-level `dut_top`
  and `tb_top` strings.
- **Major bump** (`1.x` → `2.0`): any field rename, retype, or
  removal. The schema file is republished as `view-v2.json`; the
  v1 schema stays in the tree for the deprecation window. The
  producer emits both for at least one minor of rtl-buddy-view
  before v1 is retired.

When v2 is first proposed, file an issue describing the breaking
change and the migration window before merging the producer-side
change.

## 10. Related docs

- [`overlays.md`](overlays.md) — protocol for writing a third-party
  overlay whose data lands under `node.overlays.<name>`.
- [`hub-protocol.md`](hub-protocol.md) — hub event schema. The
  `link` URIs in `view.json` are dispatched as `select.src` events
  by the hub.
- README — user-facing `--format json` quick reference.
