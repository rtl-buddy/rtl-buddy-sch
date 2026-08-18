# `elk.json` v1 reference (schematic payload)

`elk.json` is the machine-readable output of `rtl-buddy-view
--format elk` — an **engine-neutral schematic graph**: blocks, their
full pinouts, and the net bundles between them, with no layout and no
drawing in it. It is the analyzer half of the ELK schematic epic
([rtl-buddy-sch#163](https://github.com/rtl-buddy/rtl-buddy-sch/issues/163)),
phase P1; the SPA's elkjs canvas (P2) is its first consumer.

The producer is
[`src/rtl_buddy_view/elk_export.py`](../src/rtl_buddy_view/elk_export.py),
which pins the contract as `ELK_CONTRACT` / `NODE_CONTRACT` /
`NODE_RB_CONTRACT` / `PORT_CONTRACT` / `PORT_RB_CONTRACT` /
`EDGE_CONTRACT` / `EDGE_RB_CONTRACT`. This document is the human
form of the same promise; the tripwire is
[`tests/test_elk_export.py::test_elk_contract_keys_present_and_typed`](../tests/test_elk_export.py).

## 0. Why a third artifact

| | [`view.json`](view-json-v1.md) | [`graph.json`](graph-json-v1.md) | `elk.json` |
| --- | --- | --- | --- |
| Shape | one node per elaborated instance, flat list | node-link knowledge graph | **nested** node tree with ports + edges |
| Question | "what is in this design, with overlays" | "what relates to what" | "what does the schematic look like" |
| Connectivity | parent→child | typed relations | **sibling dataflow, per scope** |
| Pins | port connections as text | `port:` nodes | **ELK ports with direction + width** |
| Overlays | carries them all | never | clock only (P4 adds the rest) |

The nesting is the point: ELK lays out compound nodes natively, so
the hierarchy the analyzer already knows becomes the container
structure the renderer draws, with each scope's own dataflow inside
its own box.

## 1. The envelope is an ELK node

There is no wrapper object. The top-level dict **is** the ELK root
node, which is what
[elkjs](https://github.com/kieler/elkjs)`.layout()` accepts verbatim:

```jsonc
{
  "id": "demo_tiny_alu_subsys_top",
  "rb": {
    "instance_name": null,
    "module_name": "demo_tiny_alu_subsys_top",
    "is_blackbox": false,
    "param_overrides": [],
    "clock": null,
    "export": {
      "schema_version": 1,
      "generator": { "tool": "rtl-buddy-view", "version": "0.7.2" },
      "design": { "top": "demo_tiny_alu_subsys_top" }
    }
  },
  "ports": [ /* § 3 */ ],
  "children": [ /* § 2, recursive */ ],
  "edges": [ /* § 4 */ ]
}
```

`rb.export` appears on the **root only** — every other object in the
tree is a plain node, port or edge.

### 1.1 CLI

```bash
rtl-buddy-view \
    --top demo_tiny_alu_subsys_top \
    --filelist artefacts/hier/demo_tiny_alu_subsys_top/hier.f \
    --format elk --output elk.json
```

Flags behave exactly as they do for the other formats (`--top` /
`--tb-top` / `--filelist` / `--frontend` / `--output` / `--overlay`),
and the exit-code table is unchanged: `0` success, `1` unresolved top
/ parse failure / bad overlay, `2` missing required option or an
unimplemented frontend.

## 2. The `rb` namespace

Everything rtl-buddy knows that ELK does not lives under a single
`rb` key on each object. ELK ignores unknown keys, so the payload
stays a valid ELK graph while carrying the schematic semantics a
renderer needs.

Per **node**:

| Key | Type | Notes |
| --- | ---- | ----- |
| `instance_name` | string \| null | The refdes (`u_afifo`). `null` on the root, which is not instantiated. |
| `module_name` | string | The type name, drawn centred inside the box. |
| `is_blackbox` | bool | The module was never declared in the parsed sources. |
| `param_overrides` | array | `[[name, value], …]` in **declaration order**. `name` is `null` for a positional override on a declaration we couldn't resolve. |
| `clock` | string \| null | Predominant clock from the `clock` overlay; `null` without one. |
| `export` | object | Root only — see § 1. |

Node `id` is the **instance path** (`top.u_afifo.u_sync_wptr`) — the
same identity `view.json` uses for `nodes[].id` and the hub resolver
keys on, so cross-artifact joins are id equality rather than name
matching.

> **Coming in #162.** The `rbsch:` pragma work adds a
> `display_label` to `HierNode`. When it lands it surfaces here as
> `rb.display_label`, and a consumer's box title becomes
> `rb.display_label || rb.instance_name`.

## 3. Ports are the whole pinout

```jsonc
{
  "id": "demo_tiny_alu_subsys_top.u_afifo:rd_empty",
  "rb": {
    "name": "rd_empty",
    "direction": "output",
    "is_clock": false,
    "is_reset": false,
    "width": 1,
    "connected": true
  }
}
```

| Key | Type | Notes |
| --- | ---- | ----- |
| `name` | string | The formal port name. |
| `direction` | `"input"` \| `"output"` \| `"inout"` \| null | `null` for an interface-bundle port and for a blackbox pin. |
| `is_clock` / `is_reset` | bool | Name-shaped, using the conservative token form (`clk`/`clock`, `rst`/`reset` as an underscore-delimited token). P2/P4 draw the chevron and bubble glyphs off these. |
| `width` | int \| null | Declared bit width; `null` when the bound is parameterised (`[PTR_W-1:0]`), the type is an aggregate, or the port is an interface bundle. |
| `connected` | bool | The port is bound at this instance's binding site. Always `true` on the root, which has no binding site. |

Two rules matter here:

- **Every declared port is exported**, not just the connected ones.
  A schematic symbol shows its whole pinout; an unbound `en` pin is
  information (it is why the block never enables), and a consumer
  cannot recover a pin the exporter dropped.
- **A blackbox gets the pinout its binding site reveals** — the
  named formals from `.probe(w)` and friends, sorted by name, with a
  `null` direction and width. Nothing is claimed that wasn't seen.

Port `id` is `<instance path>:<port name>`. Instance paths are
dot-separated, so the colon makes a port id unambiguous against
every node id.

## 4. Edges are net bundles, per scope

Each node's `edges` array holds the dataflow **among that node's own
children** (plus its own ports), computed by
[`connectivity.scope_connectivity`](../src/rtl_buddy_view/connectivity.py).
An edge stands for a whole bundle: every net running between one
ordered endpoint pair.

```jsonc
{
  "id": "demo_tiny_alu_subsys_top.u_csr:hwif_out->demo_tiny_alu_subsys_top.u_hs_cmd",
  "sources": ["demo_tiny_alu_subsys_top.u_csr:hwif_out"],
  "targets": ["demo_tiny_alu_subsys_top.u_hs_cmd"],
  "rb": {
    "nets": ["cmd_payload_apb", "cmd_src_valid"],
    "bits": null,
    "src_pins": ["hwif_out"],
    "dst_pins": ["src_data", "src_valid"]
  }
}
```

| Key | Type | Notes |
| --- | ---- | ----- |
| `sources` / `targets` | array of 1 id | A **port id** when the formal pin at that end is unambiguous (exactly one pin), the **node id** otherwise. |
| `rb.nets` | array of string | Net names the bundle carries, sorted. |
| `rb.bits` | int \| null | Total width: the sum of each net's width, taken from that net's **driving** pin. `null` when any net in the bundle is of unknown width — a partial sum would read as a real bus width. |
| `rb.src_pins` / `rb.dst_pins` | array of string | The formal pins the bundle attaches to at each end, sorted. Populated even when the endpoint degrades to a node id, so the label is never lost. |

Why the degradation: one edge may stand for two pins at once
(`wr_en` *and* `wr_data` from the same driver). There is no single
pin to land the wire on, so the reference falls back to the box and
the consumer attaches at the border — while `rb.src_pins` /
`rb.dst_pins` still name what it carries.

Clock and reset trees are **not** in `edges`: they fan out to nearly
every block and bury the dataflow (the same filter `--block-diagram`
applies). The pins themselves are still exported, flagged
`is_clock` / `is_reset`, which is what lets a renderer draw a clock
symbol on a block without routing the clock net.

Edge `id` is `<source ref>-><target ref>`, unique within a scope
because bundling collapses each ordered endpoint pair to one edge.

## 5. Consumer contract (P2 and anyone else)

1. **Layout options are yours.** The payload contains no
   `layoutOptions`, no `elk.algorithm`, no `elk.port.side`, no sizes.
   Mapping `rb.direction` to `WEST`/`EAST`, choosing `FIXED_SIDE`,
   picking spacings, and measuring label text in the font you will
   actually render are all presentation decisions — and the consumer
   is the only party that knows its font metrics. The exporter
   carries the *facts*; the renderer decides the picture. A test
   (`test_payload_carries_no_layout_options`) keeps it that way.
2. **Nothing volatile.** Same rule as `graph.json`: no test status,
   no seeds, no artefact paths, no timestamps. The payload changes
   only when the RTL changes, which is what makes it cacheable and
   diffable.
3. **Deterministic bytes.** Children sort by instance path, edges by
   id, net and pin lists are sorted, ports keep their declaration
   order (the pinout a datasheet prints). Two runs over the same
   sources produce identical bytes.
4. **Additive evolution.** New `rb` keys may appear at any time; a
   consumer must ignore what it doesn't recognise. `schema_version`
   bumps only when a pinned key is removed or retyped.
5. **Ids are the join.** Node ids are instance paths, so a selection
   in the schematic maps directly onto `view.json` nodes, the hub's
   resolver, and `graph.json`'s `inst:<top>/<path>` (strip the
   prefix).

## 6. Known limits

Inherited from the structural (non-elaborating) model — see
`connectivity.py` for the full list:

- A net driven only by a continuous assign from a struct/interface
  field (`assign cmd_payload_apb = {hwif_out…}`) has no driving *pin*,
  so `rb.bits` is `null` even when the RTL declares the net's width
  locally. Module-internal net declarations aren't captured by the
  extractor yet; capturing them is the fix, not guessing from the
  sink side.
- A parameterised port (`logic [WIDTH-1:0]`) has an unknown width by
  design: rtl-buddy-view does not evaluate parameter expressions, and
  a wrong bus width is worse than an unlabelled wire.
- Positional port connections contribute no dataflow (the child's
  port order isn't authoritative enough), though they do mark a port
  `connected`.
- Procedural (`always`) logic contributes no edges; only continuous
  assigns hop net aliases.
