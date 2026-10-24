# `rbsch` pragmas — author-guided diagrams

Everything this tool draws today is *inferred*: hierarchy from
instantiations, dataflow from net names, boxes from modules.
Inference has a ceiling — it cannot know which blocks are the story
and which are noise, or what a block should be *called* in a design
document. Pragmas are how the author says so, in the RTL, where the
statement gets reviewed and refactored alongside the code it
describes.

> Epic [#159](https://github.com/rtl-buddy/rtl-buddy-sch/issues/159)
> ships this vocabulary in five phases: abstraction control and
> semantic labels (1), net classification (2), named bundles (3),
> layout hints (4) and blackbox pin pinning (5) — all documented
> below. [#196](https://github.com/rtl-buddy/rtl-buddy-sch/issues/196)
> added the block-diagram presentation words (`params=`, `hide` and
> `bundle=` on root ports, `color=` on clocks).

## The `rb` prefix convention

All rtl-buddy in-source pragmas begin with **`rb`**, and the suffix
names the tool that owns the vocabulary:

| prefix | owner |
|---|---|
| `rbsch` | this repo (rtl-buddy-sch / `rtl-buddy-view`) |
| `rbcdc` | rtl-buddy-cdc (reserved) |
| `rbxeno` | xeno (reserved) |

A tool ignores every prefix but its own, so one design can carry
several without either tool guessing at intent.

They are **magic comments, not SystemVerilog attributes**. Attributes
aren't legal in every position (you can't attach one to a single net
inside a concatenation), they leak into synthesis flows, and
rtl-buddy-cdc already claims attribute namespace with
`(* cdc_handshake *)`. A comment is legal anywhere and invisible to
every other tool.

## Syntax

```systemverilog
// rbsch: <word> <word> <key>="<value>"
```

- Bare words are flags. They come in two families: the **box**
  vocabulary shapes modules and instances (`leaf`, `collapse`,
  `hide`), the **net** vocabulary classifies wires (`clock`,
  `reset`, `data`, `main`, `side`).
- `key=value` words carry a value: `label="CSR block (PeakRDL)"`,
  `rank=2`, `group=frontend` and `params=W,DEPTH` (box),
  `bundle=cmd_bus` and `color=blue` (net).
  Quoting follows shell rules (single or double quotes), so a value
  with spaces must be quoted.
- Only `//` line comments are scanned; `/* rbsch: … */`
  is not a pragma.
- Order doesn't matter, and several words may share one comment.

Unknown words never fail the run. They are collected as warnings on
stderr naming the known vocabulary, so a typo degrades to "no hint",
never to "no diagram":

```
hints: /path/top.sv:42: unknown rbsch key 'collpase'; known flags:
collapse, hide, leaf; known keys: label=…
```

## Association rules

A pragma binds to exactly one declaration:

1. **Trailing** — code to the left of the comment: it binds to the
   declaration *starting on that same line*.

   ```systemverilog
   ip_cdc_sync #(.STAGES(2)) u_sync_src (  // rbsch: collapse
   ```

2. **Standalone** — the comment is the whole line: it binds to the
   *next* declaration below it, in the same file.

   ```systemverilog
   // rbsch: leaf
   module ip_cdc_sync #(parameter int STAGES = 2) (
   ```

A trailing pragma on a line that starts no declaration binds to
nothing and warns. That is deliberate: falling through to the next
declaration would let a stray comment silently reshape a block
several lines away.

**Each vocabulary family associates against declarations of its own
kind.** Box words (`leaf` / `collapse` / `hide` / `label`) see only
module and instance declarations; net words (`clock` / `reset` /
`data` / `main` / `side`) see only net and port declarations. A
standalone `// rbsch: collapse` sitting above a `logic` declaration
still reaches the instantiation below it — adding net vocabulary to
the language cannot re-bind a phase-1 pragma — and a payload mixing
both families binds each half independently.

Two net-specific refinements:

- A net pragma covers **every declarator starting on its line**:
  `logic a, b;  // rbsch: clock` classifies both `a` and `b`. A
  standalone net pragma covers the next declaration *line* — one
  line, never a run, so one comment cannot silently reclassify half
  a module. The one exception is `bundle=`: a bundle is by
  definition a statement about a group, so its standalone form
  covers the **contiguous run** of net declarations below it (a
  blank line or any non-declaration line ends the run), and any
  other net words on the same pragma ride along with it.
- Ports are net targets too: a clock is very often a port of the
  scope being drawn, and a trailing `// rbsch: clock` on the port
  declaration is the natural spelling.
- `hide` is a box word, but it can also name a **port** (#196). It
  keeps every binding it has always had — the instance on its line,
  or the next module / instance below it even past a plain `logic`
  declaration — and binds to a port instead only where a port
  declaration is the nearer target: on the pragma's own line with no
  module or instance starting there, or, for a standalone pragma, the
  next declaration below it before any module or instance starts.
- So are **interfaces** — an interface instance (`cfg_if cfg ();`)
  or an interface port of the scope. The block diagram treats each
  member an instance binds (`.en(cfg.a_en)`, `.sta(q[1].c_sta)`) as
  a net of its own, named by its member path; none of those members
  has a declaration in the scope to carry a pragma, so a net hint on
  the interface speaks for **every member** of it. A hint keyed on the
  member path itself, where one exists, wins over the interface's.
  Classification without a hint reads the member's own name: `bus.clk`
  is a clock, `cfg.a_en` is data.

Association uses the `SourceLocation` every extractor dataclass
already carries, so there is no CST surgery involved — a per-file
line scan resolves against extracted locations.

## Phase 1 vocabulary

| pragma | valid on | effect |
|---|---|---|
| `leaf` | module declaration | no instance of this module ever expands |
| `collapse` | instance | this one instance renders as a leaf box |
| `hide` | instance | omit this instance and its subtree entirely |
| `label="…"` | module or instance | box title becomes the role name; instance and module names demote to subtitles |

`leaf` on an instance, or `collapse` / `hide` on a module, is a
warning naming where the pragma belongs — not an error.

`leaf`, `collapse` and `hide` transform the **graph**, not the
rendering, so `dot`, `tree`, `mermaid` and `json` all agree on what
exists. `label` rides along on the node and is consumed by the dot
renderer in phase 1 (tree and mermaid pick it up later).

The top node is never hidden or collapsed. `hide` and `collapse`
can't even name it — they key on the parent module and the top has
no parent — so the reachable case is `leaf` on the top module
itself, which warns and is ignored. A figure with nothing in it is
never what the author meant.

When two pragmas name the same target, they merge; an instance
`label` beats a module `label` on the same node (the instance is the
more specific statement).

## Phase 2 vocabulary — net classification

The block-diagram connectivity analyzer decides by *name regex*
which nets are clock/reset plumbing (dropped — they fan out to
nearly every block and bury the dataflow) and which are data
(drawn). Inference has the same ceiling here: a clock named `tick`
leaks into the dataflow, and a data net named `clk_result` is
wrongly suppressed. The net vocabulary overrides the classifier in
both directions, and lets the author say which wires *matter*:

| pragma | valid on | effect |
|---|---|---|
| `clock` / `reset` | net or port declaration | the net is clock/reset plumbing, whatever it is called — its group drops out of the dataflow |
| `data` | net or port declaration | the net is data, whatever it is called — its group survives the clock-tree filter |
| `main` | net declaration | embolden the edge carrying this net: the primary datapath |
| `side` | net declaration | thin and gray the edge: CSR / status wiring that shouldn't compete with the main path |

- `clock`, `reset` and `data` are mutually exclusive on one pragma,
  as are `main` and `side`; a conflict warns and drops that half of
  the payload.
- `main` and `side` **imply data** — they are statements about a
  dataflow edge, so a hinted net survives the clock filter without
  needing the second word.
- A dataflow edge bundles every net connecting the same pair of
  endpoints. The bundle is emphasized when any of its nets is, and
  `main` beats `side` within one bundle — the author called part of
  it the money path, and thinning the whole edge would hide that.
- An edge into a **monitor modport** (all inputs, listening to both
  halves of a handshake; see `docs/elk-json-v1.md` § 4.1) is `side`
  by default. Any hinted emphasis on its nets wins over that default.
- Net hints are consumed by the **dataflow** analyses only — the
  `--block-diagram` dot mode and the ELK export (so the SPA canvas
  and the figure agree on what exists). Tree, mermaid and JSON
  hierarchy output are unaffected. The ELK payload changes
  structurally (which edges exist) but gains no new keys; emphasis
  styling there is a follow-up behind its own schema review.

```systemverilog
module nh_top (
    input  logic       clk,
    input  logic       tick,        // rbsch: clock
    input  logic [7:0] din,
    output logic [7:0] clk_result   // rbsch: data
);

  logic [7:0] stage_q;      // rbsch: main
  logic       done_status;  // rbsch: side
```

## Phase 3 vocabulary — named bundles

A handshake is one interface but many nets — and two *directions*,
because ready flows against the data. Inferred edges can only show
what the wires do; `bundle=` lets the author say what they *mean*:

| pragma | valid on | effect |
|---|---|---|
| `bundle=<name>` | net declaration(s) | the nets form one named wire group: their edge is labeled `<name>` instead of the net list, drawn thick, and a same-named return path folds into the data direction |

```systemverilog
  // rbsch: bundle=cmd_bus
  logic        cmd_valid;
  logic [15:0] cmd_data;
  logic        cmd_ready;
```

- The standalone form covers the **contiguous declaration run**
  (see the association rules); the trailing form covers its line.
- An edge takes the bundle name only when **every** net on it
  carries the same `bundle=` — all-or-nothing, because naming an
  edge that also hauls unrelated nets would claim more than the
  author said. A mixed edge keeps its plain net-list label.
- When the same bundle name appears on edges in **both** directions
  between one pair of blocks, the return path folds into the data
  direction — the side carrying more payload wins (compared in bits
  when both widths are known, in net count otherwise), its nets and
  pins merge in, and the diagram shows one thick edge pointing the
  way the data goes. A symmetric tie keeps both edges: guessing the
  data direction would point an arrow the wrong way on exactly the
  interfaces where the reader can't tell either.
- The bus slash (`bits`) keeps describing the kept direction's nets
  only: it is the payload width, not payload plus handshake.
- An explicit `main` / `side` emphasis on a member net wins over the
  bundle's default thickness — the per-net word is the more specific
  statement.
- The member nets stay in the JSON / ELK payloads; only the label
  changes.
- On an **interface**, `bundle=` groups its members back together:
  each member is its own net (labelled `cfg.a_en`, `cfg.b_en`, …), and
  the interface's bundle name is inherited by all of them, so the
  edges between two blocks fold into one named edge exactly as a
  run of bundled `logic` declarations would.

  ```systemverilog
    cfg_if cfg ();  // rbsch: bundle=cfg_bus
    csr  u_csr  (.a_en(cfg.a_en), .b_en(cfg.b_en), .c_sta(cfg.c_sta));
    peer u_peer (.a_en(cfg.a_en), .b_en(cfg.b_en), .c_sta(cfg.c_sta));
    // one edge, u_csr -> u_peer, labelled cfg_bus
  ```

## Phase 4 vocabulary — layout hints

Graphviz lays out what the wires imply; sometimes the author knows
better how the figure should *read*:

| pragma | valid on | effect |
|---|---|---|
| `rank=<n>` | instance | bias the column order: lower ranks land further left, so left→right reads as pipeline order |
| `group=<name>` | instance | draw a dashed virtual container around every sibling instance sharing the name — a grouping that isn't a hierarchy level (`cclk_domain`, `frontend`) |

```systemverilog
  ld_stage u_fetch  (  // rbsch: rank=1 group=frontend
      …
  );
  ld_stage u_decode (  // rbsch: rank=2 group=frontend
      …
  );
  ld_stage u_execute (  // rbsch: rank=3
      …
  );
```

- Both apply to the **cluster-tree dot modes** (`--block-diagram`
  and the JSON-embedded cluster layout); tree, mermaid and JSON
  hierarchy output are untouched.
- `rank=` is realized as invisible ordering edges between successive
  rank groups — a *bias*, not a guarantee: real dataflow edges still
  participate in ranking, which is exactly right, because the author
  is ordering the pipeline, not overruling its wiring. Ranks are
  scoped to one parent: `rank=1` in two different scopes never
  interact.
- `group=` containers render dashed and neutral — an annotation,
  never a fake module boundary. The container opens where its first
  member lands in the largest-first emission order, so members stay
  together while everything else keeps the established layout. The
  group name is scoped to the parent instance; the same name in two
  scopes yields two containers.
- On a module declaration both warn — like `collapse`, they are
  statements about one *placement*, and a module has many.
- Sidecar: instance entries take `"rank"` (an integer — a JSON
  `true` is rejected) and `"group"` (a string, `""` being the
  explicit revoke of an in-source membership).

## Phase 5 vocabulary — blackbox pin directions

A child the module table cannot resolve — vendor IP, an encrypted
netlist, a macro cell — has unknown pin directions. The analyzer
deliberately under-draws there: an unknown pin joins a wire group as
a *sink* when the group already has a known driver, and is dropped
otherwise, because a fabricated direction is worse than a missing
edge. `in=` / `out=` recover what the author knows:

| pragma | valid on | effect |
|---|---|---|
| `in="pin_a,pin_b"` / `out="pin_c"` | instance or module declaration | pin the named formals' directions, recovering dataflow edges for pins of unresolved modules |

```systemverilog
  vendor_rom u_rom (  // rbsch: in=addr out=q
      .addr(addr_in),
      .q(rom_q)
  );
```

- Values are comma-separated formal pin names; whitespace around
  names is ignored. A pin spelled on both sides warns and drops from
  both — a contradiction is not a direction.
- Hints are consulted **only where resolution came up empty**: a
  resolved module's own declaration always wins, so a stale hint can
  never flip a real direction.
- The sidecar is the natural home for vendor IP (its source can't
  carry a comment): key the **module** entry by the blackbox's name
  and every placement inherits the pins. Instance-level pin hints,
  when any are present, replace the module-level ones wholesale —
  one author statement per placement.
- Sidecar spelling is a JSON array per key (`"in": ["addr"]`,
  `"out": ["q"]`); `[]` is the explicit revoke of an in-source list.
- Deliberately no `inout`: the hint exists to recover a *dataflow*
  direction, and a bidirectional claim recovers nothing.

## Presentation vocabulary — integration tops (#196)

An integration top passes most parameters explicitly and has dozens
of ports, so a raw `--block-diagram` of one is boxes full of
parameter tables framed by two long columns of flags, many with
nothing attached. These words, plus the `--params` and
`--prune-flags` CLI flags, trim it to a figure fit for a document.

| pragma | valid on | effect |
|---|---|---|
| `params=none` / `params=all` / `params="W,DEPTH"` | module or instance | which parameter overrides the box shows: none, all, or just the named ones |
| `hide` | port of the diagram root | no boundary flag for the port, and no edge to it |
| `bundle=<name>` | ports of the diagram root | the ports render as **one** flag labelled `<name>` |
| `color=<name or #rrggbb>` | clock net or port | the clock's fill / legend colour |

```systemverilog
module soc_top (
    // rbsch: bundle=s_dom
    input  logic        s_clk,    // rbsch: clock color=blue
    input  logic        s_rst_n,

    // rbsch: bundle=apb
    input  logic        psel,
    input  logic [11:0] paddr,
    input  logic [31:0] pwdata,
    output logic [31:0] prdata,
    output logic        pready,

    input  logic        dbg_en,   // rbsch: hide
    …
);
  apb_regs #(.AW(12), .DW(32), .DEPTH(16)) u_regs (…);  // rbsch: params=none
```

### `params=`

- `none` hides every parameter row, `all` shows every one (the
  default), and a comma-separated name list keeps just those rows in
  the source's override order. A name list drops positional
  overrides: they have no name to match.
- On a module it covers every placement; an instance's own `params=`
  wins, like `label`. A name the module doesn't declare warns (and is
  kept, in case the module comes from a different filelist next run).
- Display only. `Instance.param_overrides`, the JSON nodes' contract
  `param_overrides` and the ELK payload keep every override. The dot
  renderer (every mode, including the JSON-embedded layout) and the
  mermaid renderer honour it.
- The CLI's `--params none` (dot and mermaid) is the figure-wide
  switch: no parameter rows anywhere, whatever the pragmas say.
  `--params all`, the default, is the pre-#196 output narrowed only
  by `params=` pragmas.

### Root ports: `hide`, `bundle=` and `--prune-flags`

Both words act on the **block diagram's root frame** (`--block-diagram`),
the one place ports draw as boundary flags; on a port of any other
module they are inert until that module is the root.

- `hide` drops the flag, every dataflow edge to it, and any CDC / RDC
  overlay arrow out of it (the destination keeps its `⚠CDC` / `⚠RDC`
  summary). The edges go too, so no edge can name a flag that
  doesn't exist.
- Ports sharing a `bundle=` name — an APB group, a domain's clock and
  reset — render as **one** flag labelled with the bundle name, placed
  where its first member is declared. It uses the net `bundle=`
  semantics: the flag faces the bundle's payload direction (more bits
  wins, port count when a member's width is unknown; a tie is a `↔`
  flag), the member edges to one block merge into one thick edge
  labelled with the bundle name, and its return path (`prdata`,
  `pready`) folds into the data direction. Overlay arrows out of a
  bundled port leave the bundle's flag.
- `--prune-flags` (CLI, block diagram only) drops every root flag no
  edge lands on — the clock and reset ports whose groups the
  classifier drops are the usual case. A flag any edge or CDC / RDC
  arrow names is kept. It is opt-in: a flag with no edge still
  documents the block's interface, and the default output stays
  byte-identical for existing figures.
- The ELK export (`--format elk`) still lists every root pin: it is
  the schematic canvas's data, not the document figure.

### `color=`

- `// rbsch: clock color=blue` on a clock's port (or net) of the
  diagram root — or of the clock map's `design_top` — pins that
  clock's colour. It colours the clock of the same name and every
  clock whose `ports` list names the net.
- Values are a swatch name (`blue`, `green`, `yellow`, `pink`,
  `purple`, `orange`, `cyan` — the palette's pastels, not X11's
  saturated colours) or `#rrggbb`. Anything else warns and is
  dropped. `color=` on a net the same pragma calls `data` or `reset`
  warns: only clocks are coloured.
- The `clock-colors` overlay (`--overlay clock-colors=map.json`) does
  the same keyed by clock name, for a document set whose figures must
  agree; it wins over a pragma.
- Every clock without a chosen colour gets a **distinct** swatch:
  the swatches are dealt in sorted clock-name order, skipping any a
  chosen colour took. Only a figure with more clocks than free
  swatches falls back to the stable name hash. The node fills,
  cluster outlines, two-tone crossing fills, crossing grid cells,
  signal edges and `--clock-legend` of one dot figure all read the
  same assignment, as does `--format mermaid`.

## Sidecar JSON

Vendor and generated IP can't take a comment. The same vocabulary
loads from a JSON sidecar through the overlay registry:

```
rtl-buddy-view --top demo_tiny_alu_subsys_top \
    --filelist demo.f --format dot --block-diagram \
    --overlay hints=docs/demo.hints.json
```

```json
{
  "schema_version": "1.2",
  "modules": {
    "ip_cdc_sync": { "leaf": true },
    "demo_tiny_alu_subsys_csr": { "label": "CSR block (PeakRDL)" },
    "vendor_rom": { "in": ["addr"], "out": ["q"], "params": "none" }
  },
  "instances": {
    "demo_tiny_alu_subsys_top.u_afifo": { "collapse": true, "params": ["DEPTH"] },
    "demo_tiny_alu_subsys_top.u_dbg_tap": { "hide": true },
    "demo_tiny_alu_subsys_top.u_compute": { "label": "ALU datapath", "rank": 2 }
  },
  "nets": {
    "demo_tiny_alu_subsys_top.tick": { "classification": "clock", "color": "blue" },
    "demo_tiny_alu_subsys_top.result_bus": { "emphasis": "main" },
    "demo_tiny_alu_subsys_top.cmd_valid": { "bundle": "cmd_bus" },
    "demo_tiny_alu_subsys_top.dbg_en": { "hide": true }
  }
}
```

- `modules` is keyed by module name; `instances` by
  `"<parent_module>.<instance_name>"`; `nets` (phase 2) by
  `"<module>.<net_name>"` — the same pairs the in-source paths
  resolve to. No key half may contain a dot.
- A net entry spells the families as fields: `"classification"` is
  one of `"clock"` / `"reset"` / `"data"`, `"emphasis"` is `"main"`
  or `"side"` (invalid values raise — they would otherwise silently
  classify nothing), and `"bundle"` is the wire-group name, where
  `""` is the explicit revoke of an in-source membership. `"hide"`
  (a boolean) hides a root port's flag, and `"color"` is a clock
  colour in the pragma's spellings (`""` revokes; anything else that
  isn't a colour raises).
- `"params"` (modules and instances, schema 1.2) is `"all"`,
  `"none"` or an array of parameter names; `[]` means `"none"`.
- Every entry field is optional. An absent field means "says
  nothing", which is what lets a sidecar `"leaf": false` *revoke* an
  in-source `leaf` (or `"classification": "data"` revoke an
  in-source `clock`) rather than only ever adding hints.
- `schema_version` major must be `1`; minor drift is tolerated.
- Unknown entry fields warn (same tolerance as pragmas); wrong
  *types* and malformed keys raise, because a sidecar is an artefact
  you deliberately pointed at and a silently ignored one is worse
  than a loud failure.

**The sidecar wins on conflict**, field by field: a sidecar that only
renames a block keeps the in-source `collapse` on that same
instance.

## Escape hatch

`--no-pragmas` disables in-source scanning for one run — the design
as written, no author edits applied. It does not affect
`--overlay hints=`, which stays explicit.

## Worked example

```systemverilog
// demo_tiny_alu_subsys_top.sv

// rbsch: leaf
module ip_cdc_sync #(parameter int STAGES = 2) (…);
  …
endmodule

module demo_tiny_alu_subsys_top (…);

  // rbsch: label="CSR block (PeakRDL)"
  demo_tiny_alu_subsys_csr u_csr (…);

  // rbsch: label="ALU datapath"
  demo_tiny_alu_subsys_compute u_compute (…);

  ip_async_fifo #(.DEPTH(8)) u_afifo (  // rbsch: collapse
      …
  );

  // rbsch: hide
  dbg_tap u_dbg_tap (…);

endmodule
```

Rendered with:

```
rtl-buddy-view --top demo_tiny_alu_subsys_top \
    --filelist demo_tiny_alu_subsys.f \
    --format dot --block-diagram | dot -Tsvg -o subsys.svg
```

- `leaf` on `ip_cdc_sync` folds every synchronizer instance — there
  are four — into single boxes, wherever they appear.
- `collapse` on `u_afifo` hides the FIFO's internals without
  claiming the module is a leaf everywhere else.
- `hide` on the debug tap removes it and its subtree; a tie-off is
  not part of the story.
- The two `label`s put role names on the two blocks a reader
  actually cares about, with the module name kept underneath as
  provenance.

Six pragma lines take the demo subsystem from 16 boxes to a figure
that reads like the hand-drawn one.
