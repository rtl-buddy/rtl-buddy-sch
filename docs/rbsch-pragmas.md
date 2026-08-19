# `rbsch` pragmas — author-guided diagrams

Everything this tool draws today is *inferred*: hierarchy from
instantiations, dataflow from net names, boxes from modules.
Inference has a ceiling — it cannot know which blocks are the story
and which are noise, or what a block should be *called* in a design
document. Pragmas are how the author says so, in the RTL, where the
statement gets reviewed and refactored alongside the code it
describes.

> Phase 1 (epic
> [#159](https://github.com/rtl-buddy/rtl-buddy-sch/issues/159))
> ships abstraction control and semantic labels; phase 2 adds net
> classification. Bundles, layout hints and blackbox pin pinning are
> phases 3–5 of the same epic; this document grows with them.

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
- `key=value` words carry a value: `label="CSR block (PeakRDL)"`.
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
  a module.
- Ports are net targets too: a clock is very often a port of the
  scope being drawn, and a trailing `// rbsch: clock` on the port
  declaration is the natural spelling.

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
  "schema_version": "1.1",
  "modules": {
    "ip_cdc_sync": { "leaf": true },
    "demo_tiny_alu_subsys_csr": { "label": "CSR block (PeakRDL)" }
  },
  "instances": {
    "demo_tiny_alu_subsys_top.u_afifo": { "collapse": true },
    "demo_tiny_alu_subsys_top.u_dbg_tap": { "hide": true },
    "demo_tiny_alu_subsys_top.u_compute": { "label": "ALU datapath" }
  },
  "nets": {
    "demo_tiny_alu_subsys_top.tick": { "classification": "clock" },
    "demo_tiny_alu_subsys_top.result_bus": { "emphasis": "main" }
  }
}
```

- `modules` is keyed by module name; `instances` by
  `"<parent_module>.<instance_name>"`; `nets` (phase 2) by
  `"<module>.<net_name>"` — the same pairs the in-source paths
  resolve to. No key half may contain a dot.
- A net entry spells the two families as fields:
  `"classification"` is one of `"clock"` / `"reset"` / `"data"`,
  `"emphasis"` is `"main"` or `"side"`. An invalid value raises —
  it would otherwise silently classify nothing.
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
