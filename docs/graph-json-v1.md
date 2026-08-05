# `graph.json` v1 reference (design tier)

`graph.json` is the machine-readable output of `rtl-buddy-view
graph` — a node-link knowledge graph of the elaborated design.
It is the **design tier** of the cross-repo graph shared with
rtl-buddy's config tier (rtl_buddy#376) and binding tier
(rtl_buddy#378), under the design-knowledge-graph epic
(rtl_buddy#375). Issue: rtl-buddy-view#126.

The authoritative JSON Schema is
[`schemas/graph-v1.json`](../schemas/graph-v1.json); this document
is the human-readable companion, and
[`src/rtl_buddy_view/graph_export.py`](../src/rtl_buddy_view/graph_export.py)
carries the same pin points as `GRAPH_CONTRACT` /`NODE_CONTRACT` /
`LINK_CONTRACT`. All three ship in lockstep; the fixture test
[`tests/test_graph_export.py`](../tests/test_graph_export.py)
validates real exports against the schema on every PR.

## 0. `graph.json` is not `view.json`

They are different artifacts and both are needed.

| | [`view.json`](view-json-v1.md) | `graph.json` |
| --- | --- | --- |
| Shape | one node per elaborated **instance** | one node per design **entity** (module, instance, port, parameter, interface, modport) |
| Purpose | render snapshot for the SPA + hub resolver | query index / token-efficient context layer for agents |
| Edges | parent→child only | six typed relations |
| Overlays | carries them (clock, reset, coverage, wave, …) | **never** — see § 7 |
| Merging | not mergeable | node-id union across tiers |

`--format json` and `graph` read the same `ModuleTable`, so the two
files always agree: every instance in `view.json` has an `instance`
node here, and its module has a `module` (or `interface`) node.

## 1. Top-level envelope

The envelope is NetworkX node-link JSON, which is what `graphify
merge-graphs` / `graphify query` accept and what
`networkx.node_link_graph(data, edges="links")` round-trips.

```jsonc
{
  "directed": true,
  "multigraph": true,
  "graph": {
    "schema_version": 1,
    "generator": {
      "tool": "rtl-buddy-view",
      "version": "0.3.0",
      "tier": "design"
    },
    "project_root_rel": "../..",
    "design": {
      "top": "tb_top",
      "dut_top": "demo_tiny_alu",
      "tb_top": "tb_top",
      "frontend": "verible"
    }
  },
  "nodes": [ /* ... */ ],
  "links": [ /* ... */ ]
}
```

| Field | Type | Notes |
| ----- | ---- | ----- |
| `directed` | `true` | Every relation in the vocabulary is directional. |
| `multigraph` | `true` | Parallel edges are normal: one module instantiating the same child twice, one instance overriding several parameters. |
| `graph.schema_version` | integer | Major only. `1` today; a bump means the vocabulary changed incompatibly. |
| `graph.generator` | object | `{tool, version, tier}`. `tier` is `"design"` for everything rtl-buddy-view emits. |
| `graph.project_root_rel` | string | Path of the design project root **relative to the directory holding this file**. Node `file` paths resolve as `dirname(graph.json)/project_root_rel/<file>`. `"."` when the graph went to stdout. |
| `graph.design` | object | `{top, dut_top, tb_top, frontend}` — which elaboration this is. Descriptive; consumers key on node ids, not on these. |
| `nodes` | array | Sorted by `id`. |
| `links` | array | Sorted by `(source, target, type, payload)`. |

Additional properties are allowed at every level; consumers that
don't recognise a key should pass it through rather than drop it.

### 1.1 CLI

```bash
# DUT-rooted, to stdout
rtl-buddy-view graph -f design/demo_tiny_alu/test_modules.f --top demo_tiny_alu

# TB-rooted, written to the contract's artefact path
rtl-buddy-view graph \
    -f verif/demo_tiny_alu/tb.f \
    --top demo_tiny_alu --tb-top tb_top \
    --project-root . \
    -o artefacts/graph/graph.json
```

| Flag | Notes |
| ---- | ----- |
| `--filelist` / `-f` | Required. One source file per line. |
| `--top` / `-t` | DUT top module. Roots the export unless `--tb-top` is given. |
| `--tb-top` | Testbench top. Roots the export, so the SV testbench hierarchy lands in the graph. A hint that isn't a real module is auto-corrected from the elaboration (same recovery the render path does). |
| `--output` / `-o` | Default stdout. Parent dirs are created. Also writes `<stem>-meta.json` (§ 6) unless `--no-meta`. |
| `--frontend` | `verible` (default) or `slang`. |
| `--project-root` | Root that node `file` paths are relative to. Defaults to the cwd. |

At least one of `--top` / `--tb-top` is required (exit 2). Exit
codes match the render surface: `0` success, `1` bad filelist /
parse failure / unresolved top, `2` missing top or a frontend that
isn't implemented.

Note what is **not** on this verb: no `--overlay`, no
`--clock-legend`, no `--format`. See § 7.

## 2. Node objects

Every node carries exactly these keys, whatever its `type`:

```jsonc
{
  "id": "port:demo_tiny_alu.op",
  "type": "port",
  "label": "op",
  "tier": "design",
  "file": "design/demo_tiny_alu/demo_tiny_alu.sv",
  "line": 14
  // ...type-specific attributes
}
```

| Field | Type | Notes |
| ----- | ---- | ----- |
| `id` | string | Stable identity, prefixed by kind (§ 2.1). |
| `type` | string | `module` \| `instance` \| `port` \| `parameter` \| `interface` \| `modport`. |
| `label` | string | Bare entity name — the id without its scope. |
| `tier` | string | `"design"` here. Survives merging, so a merged graph can be filtered back down to one tier. |
| `file` | string \| null | Project-relative source path (absolute when the file lies outside the project root). `null` when there is no anchor — blackboxes, and entities recovered from use sites. |
| `line` | integer \| null | 1-indexed declaration / instantiation line. |

`file` + `line` are what makes the graph citable: an agent that
found a node can hand the pair to `rtl-buddy-view query
source-snippet` (or any editor) and quote the RTL instead of
guessing at it.

### 2.1 Node ids

| Kind | id | Example |
| ---- | -- | ------- |
| module | `module:<module name>` | `module:demo_tiny_alu` |
| instance | `inst:<top>/<instance path>` | `inst:tb_top/tb_top.u_dut.u_a` |
| port | `port:<owner>.<port name>` | `port:demo_tiny_alu.op` |
| parameter | `param:<owner>.<name>` | `param:fifo.WIDTH` |
| interface | `iface:<name>` | `iface:test_mem_if` |
| modport | `modport:<iface>.<name>` | `modport:test_mem_if.sub` |

`<instance path>` is the **hub resolver's instance-path identity**:
dot-separated *including* the top segment, byte-identical to
`view.json`'s `nodes[].id`. The `<top>/` prefix scopes the export,
so a DUT-rooted graph and a TB-rooted graph of the same design merge
without their instance nodes colliding. Consumers that would rather
not parse ids can read `instance_path` off the node.

`<owner>` for ports and parameters is the declaring **module or
interface** name, not an instance path — port and parameter nodes
are per-*definition*, and the per-*instance* facts (what a net binds
to, what value a parameter is overridden with) live on the
`connects` / `overrides` edges. That is what keeps the graph small
on designs that instantiate one leaf a thousand times.

### 2.2 Type-specific attributes

**`module`** — `is_blackbox` (bool), `doc` (leading comment or
null).

**`instance`** — `instance_path`, `instance_name` (null at the
root), `module` (the instantiated name), `is_blackbox`,
`is_interface`, `is_top`, `depth`.

`is_blackbox` mirrors `view.json` node-for-node. An *interface*
instance (`test_mem_if u_if();`) trips that flag, because
interfaces aren't in the module table — `is_interface` is the
discriminator that says "known entity, just not a module".

**`port`** — `owner`, `dir` (`input`/`output`/`inout`/null),
`width` (int or null), `type_text`, `port_kind`
(`wire`/`interface`/`interface_signal`), `interface_type`,
`modport`.

`width` is only filled in when it can be read straight off the
declaration: a literal packed range (`logic [7:0]` → 8, `[3:0]` →
4) or a bare **scalar keyword** — `logic` / `wire` / `reg` / `bit`,
optionally `signed`/`unsigned` — which is 1.

Everything else is `null`. A parameterized range
(`logic [WIDTH-1:0]`) yields `null` because nothing here evaluates
expressions. An aggregate or non-scalar type — `int`, `byte`,
`chandle`, a typedef, `csr_pkg::cfg_t` — yields `null` because
nothing here resolves types, and "no packed range" only means "one
bit" for a scalar keyword. A port whose `port_kind` is not `wire`
(an interface bundle such as `apb_intf.subordinate`) is `null`
unconditionally: a bundle is a set of signals, not a 1-bit port.

The rule throughout is that a wrong width is worse than a missing
one for a consumer that can follow `overrides` edges — or read
`type_text` — to the real value.

**`parameter`** — `owner`, `default` (verbatim default expression,
null when the parameter was recovered from an override site).

**`interface`** — `is_blackbox`, `signals` (names declared in the
interface body).

**`modport`** — `interface`, `is_blackbox`, `inputs`, `outputs`,
`inouts`.

### 2.3 Blackboxes

A module that no file in the filelist defines still gets a node
(`is_blackbox: true`, no `file`/`line`), and so do the ports and
parameters *observed at its instantiation sites*. Dropping them
would break the relations that matter most downstream: a
`connects` edge is where the binding tier's `drives` edge lands, so
`port:sram_sp.clk` has to exist even though `sram_sp` is a
vendor macro.

The one thing that cannot be recovered is a **positional**
connection or override on a blackbox: with no declaration there is
no order to resolve the name from, and inventing one would be a
guess. Those bindings are omitted from `links` (the instance node
and its `instance_of` edge are still there). Named bindings on
blackboxes, and positional bindings on resolved modules, are both
fully represented.

The same treatment covers every other way a **named** formal can
have no declaration behind it, so that closure (§3) always holds:

- an interface instantiated with header-port bindings
  (`apb_intf bus (.clk(apb_clk), .rst_n(apb_rst_n));`) — the
  extractor models an interface's signals, parameters and modports,
  not its own port list;
- a multi-declarator parameter header
  (`#(parameter AW = 8, DW = 32)`), which the Verible frontend
  reaches the module table as a single `<unknown>` parameter, so
  `.AW(...)` / `.DW(...)` name nothing declared;
- a formal the source names but the resolved definition does not
  declare.

In each case the named target is emitted as a stub node — right
`id`, `type`, `label`, `owner`, everything else `null` — rather
than the edge being dropped or, worse, left dangling. The absent
`file`/`line` and null `dir`/`width`/`type_text` are how a consumer
tells a recovered port from a declared one.

## 3. Link objects

```jsonc
{
  "source": "inst:tb_top/tb_top.u_dut",
  "target": "port:demo_tiny_alu.clk",
  "type": "connects",
  "confidence": "EXTRACTED",
  "formal": "clk",
  "actual": "clk_i",
  "positional": false,
  "index": 0,
  "file": "verif/demo_tiny_alu/tb_top.sv",
  "line": 42
}
```

| Field | Type | Notes |
| ----- | ---- | ----- |
| `source`, `target` | string | Node ids. Both always exist in `nodes` — the graph is closed (see §2.3 for how targets with no declaration behind them are stubbed in). |
| `type` | string | One of the six below. |
| `confidence` | string | `EXTRACTED` \| `INFERRED` \| `AMBIGUOUS`. **Always `EXTRACTED`** in this tier: every relation is a deterministic CST walk. The other two are reserved for heuristic producers (the binding tier's cocotb `dut.<x>` scan). |

| `type` | source → target | Extra attributes |
| ------ | --------------- | ---------------- |
| `instantiates` | `module:` → `module:` \| `iface:` | `instance` (the instance name creating the edge), `file`, `line` |
| `child_of` | `inst:` → `inst:` (parent) | — |
| `instance_of` | `inst:` → `module:` \| `iface:` | — |
| `connects` | `inst:` → `port:` | `formal`, `actual`, `positional`, `index`, `file`, `line` |
| `implements` | `module:` → `modport:` | `port` (the interface port pinning the modport) |
| `overrides` | `inst:` → `param:` | `parameter`, `value`, `positional`, `index`, `file`, `line` |

Notes on the two "why is it this direction" cases:

- `connects` points at the **instantiated module's** port, so the
  edge reads *"instance `u_ff` binds `counter_ff.clk` to net
  `clk`"*. Fanout of one port across the design is then a single
  in-edge query on the port node.
- `implements` comes from an interface **port**: `test_mem_if.sub m`
  on module `m3` means `m3` implements the `sub` view of
  `test_mem_if`. A bare interface port with no `.modport` suffix
  pins nothing, so it produces no edge.

`actual` is the verbatim net expression. It is the empty string for
the implicit `.port` shorthand (`.clk` with no parens) — the
extractor records what the source wrote, and expanding the
shorthand would be inference, not extraction.

## 4. Merging with other tiers

Merging is a **node-id union**. Identical ids from different tiers
are the stitch points, deliberately:

- config tier (rtl_buddy#376) emits `maps_to` edges from
  `model:…` onto `module:<name>` — the same `module:` id this file
  defines.
- binding tier (rtl_buddy#378) emits `drives` edges onto
  `port:<module>.<port>` ids from this file.

So the design tier must be merged for the other tiers' edges to
resolve, and every node here carries `tier` so a merged graph can be
filtered back apart.

```bash
graphify merge-graphs artefacts/graph/graph.json other-tier.json -o merged.json
graphify query merged.json ...
```

## 5. Determinism

Two runs over unchanged RTL produce **identical bytes**:

- `nodes` sorted by `id`; because the id prefix encodes the type,
  that sort also groups the file by kind.
- `links` sorted by `(source, target, type, <serialized payload>)`.
  The payload tie-break is what makes the order total when parallel
  edges share the first three.
- No timestamps, no absolute paths (given `--project-root`), no
  environment-dependent values, no set/dict iteration order in
  emitted bytes.

This is what makes `graph.json` diffable in review and cheap to
regenerate: the content-addressed CST cache means an unchanged
source tree re-exports without re-invoking Verible at all.

## 6. `graph-meta.json`

Writing with `-o` also writes `<stem>-meta.json` alongside (opt out
with `--no-meta`):

```jsonc
{
  "schema_version": 1,
  "tiers": {
    "design": {
      "generator": {"tool": "rtl-buddy-view", "version": "0.3.0", "tier": "design"},
      "design": {"top": "tb_top", "dut_top": "demo_tiny_alu", "tb_top": "tb_top", "frontend": "verible"},
      "inputs": [
        {"path": "design/demo_tiny_alu/demo_tiny_alu.sv", "sha256": "…", "bytes": 1834}
      ],
      "node_count": 41,
      "link_count": 58
    }
  }
}
```

The split exists so provenance can change without touching
`graph.json`: input hashes and counts belong to *this run*, the
graph belongs to *the design*. The hashes are the same SHA-256
content hashes the CST cache keys on — unchanged hashes mean every
cached CST is still valid and the export could have been skipped.
`tiers` is keyed by tier name so a merged graph's meta files can be
concatenated.

## 7. What never goes in

Volatile, per-run data — test pass/fail, seeds, coverage numbers,
artefact paths, waveform files — is **out of scope by contract**.
It belongs in the results overlay: a separate file keyed by node
id, so a failing run never produces a `graph.json` diff.

That is also why `graph` takes no `--overlay` flags even though the
render path does. Clock and reset domains sit in a grey zone (they
are derived from the RTL, not from a run) and are deliberately left
out of v1 rather than half-modelled; a future minor may add
`clock_domain` nodes with their own edge type.

## 8. JSON Schema validation

```python
from pathlib import Path
import json, jsonschema

schema = json.loads(Path("schemas/graph-v1.json").read_text())
payload = json.loads(Path("artefacts/graph/graph.json").read_text())
jsonschema.validate(payload, schema)
```

Gate on the major version explicitly —
`payload["graph"]["schema_version"] == 1` — and fail loudly if it
isn't.

## 9. Stability guarantees

Pinned for the lifetime of v1 and not changeable without a major
bump:

1. **The envelope** — `directed`, `multigraph`, `graph`, `nodes`,
   `links`, in NetworkX node-link form.
2. **Node id grammar** (§ 2.1), including `<instance path>` being
   the full dot-separated path with the top segment.
3. **The six edge types** and their source→target directions.
4. **`id` / `type` / `label` / `tier` / `file` / `line` on every
   node**, `source` / `target` / `type` / `confidence` on every
   link.
5. **`confidence: "EXTRACTED"`** on everything this producer emits.
6. **Determinism** (§ 5).

Adding node types, edge types, or attributes is *not* breaking.
Removing or retyping any of the above *is*.

## 10. Versioning policy

- **Additive change**: new node/edge types or attributes ship
  without a version bump; the schema's `enum`s grow and consumers
  ignore what they don't know. Anything that could surprise an
  existing consumer gets a note in the release.
- **Major bump** (`1` → `2`): any rename, retype, or removal from
  § 9. The schema is republished as `graph-v2.json`; `graph-v1.json`
  stays in the tree for the deprecation window.

Because the same envelope is shared across three repos, a v2
proposal is an epic-level change (rtl_buddy#375), not a
single-repo one — file the issue before the producer-side change.

## 11. Related docs

- [`view-json-v1.md`](view-json-v1.md) — the render snapshot, § 0.
- `AGENTS.md` § Cross-repo coupling — what downstream consumes.
- README — user-facing `graph` quick reference.
