# The `phys` overlay — area and power on the hierarchy

Phase 7b, [rtl-buddy/rtl-buddy-sch#22](https://github.com/rtl-buddy/rtl-buddy-sch/issues/22),
the schematic deliverable of the rtl-buddy-phys epic
[rtl-buddy/rtl_buddy#558](https://github.com/rtl-buddy/rtl_buddy/issues/558).

```bash
rtl-buddy-view --top phys_top --filelist files.f \
    --overlay phys=verif/blk/artefacts/nightly/phys-manifest.json \
    --format json --output view.json
```

The overlay is a **consumer**: it runs no tool and computes no
physics. It reads the physical model `rb synth` and `rb power` already
publish and projects it onto the elaborated hierarchy this analyzer
owns — which is the whole reason it exists as a schematic overlay
rather than another table. A flat ranking can tell you the hottest
leaf; only the hierarchy can tell you which *block* owns it.

## 1. What it consumes

Two documents, both versioned, both written by every physical run
([`docs/concepts/phys.md`](https://github.com/rtl-buddy/rtl_buddy/blob/main/docs/concepts/phys.md)
in rtl_buddy is the producer-side reference):

| | |
|---|---|
| `phys-manifest.json` | the discovery contract. `schema_version` 1. Its `model` key points at the model; `phys_dir` is how the project root is recovered, since every path in it is project-root-relative. |
| `phys-model.json` | the measurement. `schema_version` 1. `design.top`, `units`, `totals`, `provenance`, and the two halves `modules` and `instances`. |

`--overlay phys=PATH` accepts any of three things and resolves them in
this order:

1. a **manifest** — followed through its `model` pointer;
2. a **model** — read directly (no provenance about which run it was);
3. a **directory** — the manifest inside it if there is one, else the
   model.

A manifest's `model` path is resolved by counting its own `phys_dir`
components back up from the directory the manifest was found in — the
same recovery rtl_buddy's `manifest.project_root_for` makes. Two
fallbacks follow, because failing to find a model sitting next to the
manifest that names it would be a worse outcome than a slightly loose
search: the path as given, then the model's basename in the manifest's
own directory (which is where every producer in fact writes it).

### Versions are refused, not tolerated

`schema_version` is compared **exactly**, on both documents, and a
mismatch raises `PhysAnnotationsError` — surfaced by the CLI as
`overlay phys: …`, exit 1. The producer versions these documents with
a single integer bumped only for an incompatible change, so there is
no minor to be liberal about, and the halves are dereferenced by
shape: a future layout would be *misread* rather than noticed. An
absent `schema_version` is refused too — every document the producer
has ever written carries one, so its absence means this is not the
document it was taken for.

### Half-filled models warn, never fail

A synthesis fills `modules` and leaves `instances` null; a power run
does the reverse. Either is fine: the overlay contributes the half
that is present, omits the other half's keys entirely (never zeroes —
a viewer must not paint "not measured" as "burns nothing"), and puts a
sentence naming the missing command into
`overlay_meta.phys.notes`. The CLI echoes each note to stderr. Units
other than `um2` / `uW`, a duplicate module row, and a model+manifest
pair carrying different `publication` tokens are notes on the same
footing.

## 2. The two joins

The model spells `module` in **two namespaces**, and conflating them
is the bug this overlay exists to avoid (it is the naive join
rtl-buddy/rtl_buddy#558's own review removed from the producer side).

### Area and cells — RTL name to RTL name

A node's `module_name` against the `modules` row of the same name.
Yosys' `stat` counted that row for that RTL module, so this is the
correct join, and the schematic is the consumer that can make it
per-node rather than per-table.

Two properties of the producer's numbers drive everything downstream:

* **`area_um2` already rolls the submodules up.** It is therefore a
  *subtree* figure as given, and summing it across a hierarchy
  double-counts. The overlay never sums an area.
* **`cell_count` does not.** It counts a submodule *instance* as one
  cell, so it is a self-ish figure on a different footing from the
  area beside it. The overlay never rolls it up either, and never
  reports a subtree cell count — that number does not exist in the
  document.

`self_area_um2` is therefore the one figure that has to be *derived*,
and it is derived against **this view's children**: the module's area
minus its children's module areas. It is omitted whenever the
subtraction would be a guess — a blackbox child, a child the synthesis
has no row for, or a remainder floating-point residue drove below
zero. A leaf node's self area is its whole area.

All instances of one module share its area row, because area is a
property of the module. That is inherent to the document and is the
useful semantic anyway.

### Power — rootless instance path to instance path

Instance rows carry the **Liberty cell** in their `module` field
(`DFF_X1`, `NAND2_X1`) — a mapped netlist's leaves are cells, not RTL
modules. That field is joined to **nothing**. The join is on
`instance_path`, in three steps:

1. **Root it.** Model rows are rootless, relative to `design.top`.
   Prepend the top **unconditionally**, exactly as the hub's `/phy`
   pane's `withTop` does: under a top named `cpu`, the rootless row
   `cpu/alu` is a `cpu` instance *inside* the top, and a prefix test
   would misread it as already rooted.
2. **Level it.** Every `/` and every `.` is a level, whichever the
   producing tool wrote — except inside a Verilog escaped identifier
   (`\gen[0].u_x` is one name whose `.` names no level). This is
   `level_path` in rtl_buddy's `phys/query.py` and `toWirePath` in its
   `/phy` pane: three copies of one rule that must not drift.
3. **Attribute it to the nearest enclosing scope.** Walk up one level
   at a time until a view node matches. `u_sub/_64_` lands on `u_sub`,
   because the module-level view has no node for a cell. A row whose
   scope is the design top (`_18_`) lands on the top.

Then parents carry the **subtree sum** of everything below them. The
model records leaf values only — by its own rule, because a subtree
sum depends on the hierarchy the consumer projects onto — and this is
that projection.

Rows that match no scope at all (a netlist whose hierarchy differs
from the RTL as rendered) are **counted and reported** in
`overlay_meta.phys`, never silently dropped: an uncounted row is a
roll-up that quietly understates the design.

### Wrapper tops

The model's top and the rendered root need not be the same module — a
TB-rooted render (`--tb-top`) puts a testbench above the synthesised
design, and a project may synthesise a partition on its own. The
anchor search, in order:

* the rendered root **is** the model's top → root the join there;
* the model's top is **instantiated** in the view → root the join at
  the shallowest such instance (alphabetically among equals, so the
  choice is deterministic). More than one is a note, because a design
  instantiated twice has two sets of leaves and the model describes
  one of them;
* the model's top is **nowhere** in the view → the overlay attaches
  nothing. `overlay_meta.phys.attached` is `false`, `join_root` is
  `null`, and the notes say which design the model is of. A power
  figure hung on the wrong hierarchy is worse than an absent one.

A model with no recorded `design.top` falls back to the rendered root,
with a note saying a wrapper would have misplaced every row.

Above the anchor (the TB scopes) the nodes have no module row but do
carry the roll-up of everything below them — so a testbench render
still shows where the design's power is.

## 3. Channels — `node.overlays.phys`

An **optional** block, keyed on the overlay name like every other
(`docs/view-json-v1.md` §6). Absent means "the overlay had nothing to
say about this node", never an error; a key absent inside the block
means the half behind it was not measured.

```jsonc
"phys": {
  "cell_count": 2,              // the module's own cells (submodule = 1 cell)
  "area_um2": 5.586,            // the module's area — ALREADY a subtree figure
  "self_area_um2": 1.064,       // derived: area minus the children's areas
  "leakage_uw": 0.0174,         // this scope's own leaf rows …
  "internal_uw": 0.0714,
  "switching_uw": 0.0,
  "dynamic_uw": 0.0714,         // internal + switching; no producer writes it
  "total_uw": 0.0888,
  "leaf_instances": 1,
  "subtree_leakage_uw": 0.0965, // … and the roll-up of everything below
  "subtree_internal_uw": 2.3514,
  "subtree_switching_uw": 0.0675,
  "subtree_dynamic_uw": 2.4189,
  "subtree_total_uw": 2.5154,
  "subtree_leaf_instances": 2
}
```

Note the asymmetry, which is the document's and not ours: **area is a
subtree figure whose self value is derived, power is a self figure
whose subtree value is derived.** The viewer's self-vs-subtree toggle
reads `area_um2` / `subtree_total_uw` for subtree and
`self_area_um2` / `total_uw` for self.

Every figure is rounded to 6 decimal places. Float addition is
order-sensitive and the renderers owe byte-stable output, so the rows
are summed in sorted order *and* the result is quantised. 1e-6 µW is a
picowatt — below anything a producer reports.

### `overlay_meta.phys`

One envelope block, emitted whenever a model loaded — **including one
that attached nothing**, because that is precisely the state the
viewer has to be able to explain.

```jsonc
"phys": {
  "source": "…/phys-model.json",
  "manifest": "…/phys-manifest.json",   // null when a model was named directly
  "model_top": "phys_top",
  "view_root": "tb_phys_top",
  "join_root": "tb_phys_top.u_dut",     // null when nothing attached
  "attached": true,
  "units": {"area": "um2", "power": "uW"},
  "halves": {"modules": true, "instances": true},
  "totals": { … },                      // the producer's own scraped totals
  "rollup": { … },                      // this overlay's sum of the rows
  "matched_instance_rows": 4,
  "unmatched_instance_rows": 0,
  "notes": []                           // whole sentences; gate on non-empty
}
```

`totals` and `rollup` sit side by side on purpose. They come from two
different scrapes of the same run — the producer's own docstring makes
the point — so agreement is *evidence* rather than a tautology. The
test suite pins them to within 0.5%
(`tests/test_phys_overlay.py::test_rolled_up_totals_match_the_models_own_within_half_a_percent`),
which is the acceptance criterion from #22.

## 4. Renderers

| Format | Contribution |
|---|---|
| `--format json` | the per-node blocks + the envelope above. |
| `--format tree` | a trailing `  [area=1240µm² P=15µW]` per line — the module area and the subtree power roll-up. Last of the four suffixes, because it is a measurement rather than a hazard. **Byte-identical to the baseline without the overlay.** |
| `--format dot` / `mermaid` | nothing. Heat rendering is the SPA's job, and baking a fill into the layout DOT would survive the overlay toggle. |
| `--format elk` | nothing (same reason). |

## 5. The viewer

`viewer/src/overlays/phys.js`, registered in
`viewer/src/overlays/index.js`. Two channels on two axes so both can
be read at once:

* **fill saturation by area**, relative to the largest area in the
  rendered subtree (the SPA's graph *is* that subtree — descending
  re-renders it);
* an **outer ring by total power**, on the same ramp.

The ramp is the vendored hub tokens `--heat-h/s/l0/l1/none`, read
through `palette.js::heatRampColor` — the same expression the hub's
`/phy` pane computes in CSS `calc`, so the schematic and the pane
agree. There is no page-local ramp
([`design-tokens.md`](design-tokens.md)). It is *sequential*, unlike
the coverage ramp: coverage has a bad end and a good end and earns a
red→green hue sweep, while area and power only have "more".

**Self vs subtree** is `store.physScope` (`'subtree'` default), driven
by the two buttons in the overlay panel's phys legend. `GraphCanvas`
watches it beside the wave repaint and calls `repaintOverlays()` —
never a re-layout, which is #22's acceptance criterion and the reason
the state lives outside the layout block.

**The bivariate legend** is `PhysLegend.vue`: the 3×3 area × power key,
the scope toggle, the provenance line (which halves, which join root)
and every note the join had to qualify itself with. It replaces the
generic swatch list for this overlay rather than sitting beside it.

### Composition

| With | Rule |
|---|---|
| `clock` (hue) | the fill keeps the **clock's hue** and takes only its **saturation** from area (`saturateBy`). At full area the fill is exactly the clock pastel, which is what keeps the clock legend honest. This is #22's bivariate design: clock on hue, area on saturation, power on the ring. |
| `coverage` (fill) | **coverage owns the fill.** Phys contributes the ring and the legend only, and the panel prints `(coverage owns the fill; phys shows the power ring only)` so a reader is never left wondering where a channel went. Two overlays writing one `style.fill` in registry order is a race decided by alphabet, which is not a design. |
| `reset` (border) | both survive. Reset strokes the node shape; the phys ring is a **separate element** cloned off that shape and drawn over it, so reset's 2px border sits inside the 4px ring. |
| clusters | ring **yes**, fill **no**. A cluster is a subtree frame, so a roll-up belongs on it — but filling its background drowns out every leaf colour nested inside, which is why the clock and coverage overlays skip clusters outright. |

`applyOverlays` folds the enabled-overlay set into the context each
overlay sees, which is what makes the clock and coverage rules
reachable. An overlay still gets its own enabled state as its own
boolean argument; the set is the *neighbours*.

### NodeDetail

Three figures (cells, area, power, at the active scope) over two
progress bars — the node's **share of its parent's subtree** for area
and for power. That is the question a hierarchy panel is actually
asked ("is this the block that owns the area?") and the one a bare µW
figure cannot answer. The parent comes from the instance path, so a
node whose parent is outside the rendered subtree simply gets no bars.

## 6. Limitations

* **No per-cell area exists**, so there is no per-instance area
  channel and no area roll-up over leaf rows. Area is per RTL module,
  against the name Yosys counted it for. This is the model's shape,
  not a gap in the overlay.
* **No subtree cell count**, for the same reason: `cell_count` counts
  a submodule instance as one cell, so adding the column across a
  hierarchy would count the same cells at several depths.
* **One model per render.** A project holding several runs (partitions,
  corners, power modes) is a selector the hub's `/phy` pane has and
  this overlay does not —
  [rtl-buddy/rtl_buddy#568](https://github.com/rtl-buddy/rtl_buddy/issues/568)
  is where that lands. Today, name the manifest you want.
* **The hierarchy has to be the model's.** A mapped netlist that was
  flattened, or a `// rbsch: collapse` that removed a scope, leaves
  rows attributing to a shallower node (correct — it is the nearest
  enclosing scope) or to none at all (counted in
  `unmatched_instance_rows`).
* **No timing or slack channel.** Out of scope per #22; a separate
  phase if it is wanted.
* **The desktop tree carries no heat**, only the numbers. Terminal
  colour for a two-channel ramp was not worth the escape-sequence
  surface.

## 7. Related

* [`overlays.md`](overlays.md) — the plugin protocol this implements.
* [`view-json-v1.md`](view-json-v1.md) §6 — where the blocks land.
* [`design-tokens.md`](design-tokens.md) — the token rules the ramp keeps.
* rtl_buddy's `docs/concepts/phys.md` — the producer, `rb phys`, and
  the `/phy` pane this is the spatial complement of.
