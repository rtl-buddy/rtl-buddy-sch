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
| `display_label` | string \| null | The author's `rbsch: label=` for this box (#180). A consumer's box title is `display_label \|\| instance_name`; the module name stays the subtitle. `null` without a hint. |
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

## 3. Ports are the whole pinout

```jsonc
{
  "id": "demo_tiny_alu_subsys_top.u_afifo:rd_empty",
  "rb": {
    "name": "rd_empty",
    "net": "fifo_rd_empty",
    "direction": "output",
    "is_clock": false,
    "is_reset": false,
    "width": 1,
    "width_expr": null,
    "connected": true
  }
}
```

| Key | Type | Notes |
| --- | ---- | ----- |
| `name` | string | The formal port name. |
| `net` | string \| null | The net expression bound at *this instance's* binding site — the verbatim slice (`op_q`, `~rst_n`, `cmd[7:0]`), with implicit `.name` shorthand resolved to the same-named net. `null` when the formal isn't bound; on the root, a port's net is its own name. **Why it exists**: a pin whose net no sibling *pin* drives — the parent's own `always_ff` drives it, and the dataflow analyzer only hops continuous assigns (§4) — has no edge to attach to and draws as a bare stub. This is what keeps that pin traceable, and it is read off the binding site rather than inferred. A consumer should print it only when it differs from `name`. |
| `direction` | `"input"` \| `"output"` \| `"inout"` \| null | `null` for an interface-bundle port and for a blackbox pin. |
| `is_clock` / `is_reset` | bool | Name-shaped, using the conservative token form (`clk`/`clock`, `rst`/`reset` as an underscore-delimited token). P2/P4 draw the chevron and bubble glyphs off these. |
| `width` | int \| null | Declared bit width, **as this instance was parameterised** — `[WIDTH-1:0]` under `#(.WIDTH(19))` is 19. `null` when the bound doesn't resolve to an integer, the type is an aggregate, or the port is an interface bundle. See § 6.1. |
| `width_expr` | string \| null | The width in the names the declaration uses, read **as written** (`[WIDTH-1:0]` → `"WIDTH"`). Independent of `width`: a pin may carry both (`19` *and* `"WIDTH"`). `null` for a range written in integers. See § 6.2. |
| `connected` | bool | The port is bound at this instance's binding site. Always `true` on the root, which has no binding site. Note that `connected` and "has a wire in the drawing" are different questions: see `net`. |

Widths are resolved **per instance path**, not per module: the same
module instantiated twice with different overrides reports different
pin widths on its two nodes, which is what the drawing needs.

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
    "bits_expr": null,
    "src_pins": ["hwif_out"],
    "dst_pins": ["src_data", "src_valid"]
  }
}
```

| Key | Type | Notes |
| --- | ---- | ----- |
| `sources` / `targets` | array of 1 id | A **port id** when the formal pin at that end is unambiguous (exactly one pin), the **node id** otherwise. |
| `rb.nets` | array of string | Net names the bundle carries, sorted. |
| `rb.bits` | int \| null | Total width: the sum of each net's width, taken from that net's **driving** pin. **Integers only.** `null` when any net in the bundle is of unknown width — a partial sum would read as a real bus width. |
| `rb.bits_expr` | string \| null | The bundle's width in the names the declarations use (`"PTR_W"`, `"WIDTH+1"`). Independent of `bits` — both may be set. `null` when no net contributes a name, when one is unknown both ways, or when the sum wouldn't be clean. See § 6.2. |
| `rb.src_pins` / `rb.dst_pins` | array of string | The formal pins the bundle attaches to at each end, sorted. Populated even when the endpoint degrades to a node id, so the label is never lost. |
| `rb.emphasis` | `"main"` \| `"side"` \| null | The author's `rbsch` emphasis (#180): `main` is the primary datapath, `side` is CSR/status wiring. Presentation semantics only — the consumer picks the strokes. |
| `rb.bundle` | string \| null | The author's `rbsch: bundle=` name (#180). When set, the whole edge is one named interface: label it by this name instead of `rb.nets`, and note that a same-named return path (valid/ready style) has **already been folded** into this edge upstream — its nets and pins are merged in, while `bits` keeps describing the data direction only. |

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
6. **Wrap before laying out with elkjs.** The payload's root is the
   design's top module, and its `ports` are referenced by edges
   (`u_csr → <top>:apb`). That is legal ELK — a hierarchical edge to
   an ancestor's port — but elkjs's JSON importer cannot resolve
   ports owned by the node it is given as the layout root
   (`UnsupportedGraphException: the source or target … could not be
   found`, or a bare GWT null dereference with ports present).
   Consumers must wrap the payload in a synthetic container before
   calling `elk.layout()`:

   ```js
   const graph = { id: "$root", layoutOptions, children: [payload] };
   ```

   One wrapper object, verified against elkjs 0.11: the identical
   payload fails as the layout root and lays out cleanly one level
   down. The exporter deliberately does not ship the wrapper — it is
   an elkjs importer quirk, not a property of the graph, and other
   ELK consumers (ELK Java, ELK Text) accept the payload as-is.

## 6. Known limits

Inherited from the structural (non-elaborating) model — see
`connectivity.py` for the full list.

### 6.1 Bit widths

`rb.bits` resolves a net's width from three ranked sources — first
one to pin a single number wins, and a source that saw the name but
couldn't pin a number to it abstains rather than poisoning the
result:

1. the **driving pin**'s declared port type (a child `output` /
   `inout` pin, or the scope's own `input` port);
2. the scope's own **net declaration** (`logic [18:0]
   cmd_payload_apb;`) — this is what widths a net that only a
   continuous assign drives;
3. the scope's own **port declaration**, for a net that *is* an
   `output` / `inout` port fed by an assign.

Within each tier, a **parameterised** bound is re-read with that
binding site's parameters substituted before it is given up on:
`logic [WIDTH-1:0]` on a pin bound `#(.WIDTH(19))` is 19 bits, and
`logic [DATA_W-1:0]` inside an instance built `#(.DATA_W(19))` is 19
bits — not the module's declared default of 8. Substituted text is
folded by a **hand-rolled integer evaluator** (`width_expr.py`) that
understands integers, `+ - *`, parentheses and unary minus and
*nothing else*: no `eval`, no `ast`, and no approximation. The values
substituted are the child's declared defaults with the instance's own
`#(...)` overrides written over them (positional overrides resolved
against the declared order), one pass, never chasing a value's own
identifiers.

Anything still unresolved leaves `rb.bits` `null` for the whole
bundle: a partial sum would read as a real bus width to whoever draws
the slash. What stays unresolved:

- A bound that does not **fold to an integer**: a parameter that
  resolves to another symbol (`#(.WIDTH(PTR_W))` with `PTR_W` a
  computed localparam), a `$clog2` call, a division, an exponent, a
  ternary, a sized literal. A wrong bus width is still worse than an
  unlabelled wire, so nothing is guessed — but § 6.2's `width_expr` /
  `bits_expr` usually still name the width, and are emitted for the
  *resolved* cases too.
- A **localparam** bound: only header parameters are captured, so
  `localparam int PTR_W = $clog2(DEPTH)+1;` is a symbol with no value
  attached (again, § 6.2).
- A declaration the preprocessor **hides inside a macro body**: the
  extractor reads Verible's CST of the source as written, so a net
  declared by a `` `DECLARE_BUS(x) `` expansion is not there to read.
- A **user-defined type** (`my_bus_t sig;`) is read as a scalar — the
  type name carries no packed range and nothing resolves it. Same for
  a `struct` / `union` field bundle.
- An **unpacked array** (`logic [3:0] mem [0:7];`) reports the
  element width, and a **packed array** (`logic [3:0][7:0]`) reports
  the first dimension. Both are documented under-reports rather than
  silent wrong totals.
- **Procedural temporaries** — a `logic` declared inside a `function`,
  `task` or `always` block — are deliberately not captured: they are
  not module nets, and letting one shadow a real net of the same name
  would put a wrong number on a wire.
- A name declared at **two different widths** in two generate scopes
  degrades to unknown rather than picking a side.
- Two **drivers disagreeing** on width, and a driver bound to a
  concatenation (`.q({a, b})`) where the port's width belongs to no
  single net.

### 6.2 Algebraic widths (`bits_expr` / `width_expr`)

A parameterised width has **two** true answers, and the payload
carries both.

```jsonc
// ip_cdc_handshake #(.WIDTH(19)) u_hs_cmd (...);   // logic [WIDTH-1:0] src_data
{ "rb": { "name": "src_data", "width": 19, "width_expr": "WIDTH", … } }

// ip_async_fifo's gray pointers:  logic [PTR_W-1:0] wptr_gray;
// with localparam int PTR_W = $clog2(DEPTH)+1 — no integer exists
{ "rb": { "nets": ["wptr_gray"], "bits": null, "bits_expr": "PTR_W", … } }
```

**`width_expr` / `bits_expr` are derived from the range as written —
no substitution.** `[WIDTH-1:0]` is `"WIDTH"` whether or not
`#(.WIDTH(19))` folds it to 19. The parameter *name* is what the
designer wrote and what carries the design intent: `/WIDTH` tells a
reader which knob moves that bus, while `/19` is one instantiation's
elaboration detail and `/PTR_W` would be the caller's private name for
it. Substitution serves the **numeric** answer only (§ 6.1).

Corollary: a range already written in integers (`[18:0]`) has **no**
expression. A number is not algebra, and copying it into a field that
promises a name would be a second `bits`.

**The whole rule set** — deliberately tiny, deterministic, and not a
computer-algebra system:

1. **One clean pattern produces an expression**: the `lsb` folds to
   literal `0` *and* the `msb` ends in `- 1`. The answer is the rest
   of the `msb`, whitespace-normalized — `[X-1:0]` → `"X"`,
   `[X+K-1:0]` → `"X+K"`.
2. What is left must be nothing but identifiers, integers, `+ - *` and
   parentheses, and must still **contain an identifier**.
   `[$clog2(D)-1:0]`, `[X-2:0]`, `[X:0]`, `[X-1:1]` and `[18:0]` all
   produce **nothing**.
3. **Bundle summing** (edges only): each net contributes its
   expression when it has one — *not* also its number — and its number
   otherwise. Exactly one symbolic term prints, followed by the
   numeric remainder: `"PTR_W"`, `"WIDTH+1"` (a `+0` is omitted).
   **Two or more terms abstain, identical ones included**: folding
   `PTR_W + PTR_W` into `2*PTR_W` needs an equality between two source
   texts that a structural tool cannot justify (two scopes may spell
   two different localparams the same way), and every expression
   printed is one net's declared width, verbatim.
4. A bundle where no net contributes a name has **no** expression —
   `bits` says it better. A net that is unknown *both* ways sinks the
   whole bundle, exactly as it sinks `bits`.
5. Anything longer than **20 characters** abstains — past that it is a
   second label, not an annotation.

What a consumer may rely on:

- **`bits` / `width` are numeric-only and unchanged.** They are `null`
  unless the width is a real integer; the algebraic value never leaks
  into them. A consumer that wants numbers keeps getting numbers
  wherever they resolve.
- **The two are independent.** Either, neither, or *both* may be set
  for one port or one edge. A consumer that reads only `bits` is
  correct, just less informative.

**Display preference: the expression wins.** The SPA's schematic draws
`bits_expr` when it is present and `bits` otherwise, rendering the
symbolic form in *italic* so a reader can tell a name from a count.
Compactness is guaranteed upstream by rules 3 and 5, so there is
nothing for the renderer to second-guess. The one exception is
honesty, not preference: a bundle resolved to exactly **1 bit** draws
as a hairline with no slash at all — a slash means "bus", and that one
is a single wire.

Silence beats noise throughout: an unlabelled wire reads as "unknown",
while a cryptic or wrong label reads as fact.

> **Elaboration changes only half of this.** § 6.1's substitution is a
> stopgap for a frontend that doesn't elaborate; when the slang
> frontend (`frontend/slang.py`) lands, parameters resolve properly and
> it becomes redundant. The algebraic form survives it entirely — an
> elaborator gives you the number, never the name the designer wrote.

### 6.3 Dataflow

- Positional port connections contribute no dataflow (the child's
  port order isn't authoritative enough), though they do mark a port
  `connected`.
- Procedural (`always`) logic contributes no edges. Only continuous
  assignments hop net aliases — both the standalone `assign`
  statement and the declaration form (`wire w = expr;`), which is a
  continuous assignment too. A *variable* initializer
  (`logic w = expr;`) is a one-time static init, not a driver, and
  deliberately contributes nothing.
