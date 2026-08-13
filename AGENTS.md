# AGENTS.md — rtl-buddy-sch

## Role

This repo is the source-of-truth implementation of the `rtl-buddy-view`
analyzer — a Python tool that extracts the module-level hierarchy and
connectivity of a SystemVerilog design and renders it as ASCII tree /
Graphviz `.dot` / Mermaid / JSON. Comments, parameter overrides, and
source positions flow through every layer.

It is consumed by `rtl_buddy` (sibling repo) as a subprocess via
`rb hier` — shipped in
[`tools/hier_rtl_buddy_view.py`](https://github.com/rtl-buddy/rtl_buddy/blob/main/src/rtl_buddy/tools/hier_rtl_buddy_view.py)
(meta-issue [rtl-buddy/rtl_buddy#106](https://github.com/rtl-buddy/rtl_buddy/issues/106)).
Anything that breaks the JSON output schema or the CLI surface is a
downstream-breaking change — see [§ Cross-repo coupling](#cross-repo-coupling).

This is **not** a netlist/schematic visualizer. The target is the
*module-level* structural view: what instantiates what, with which
parameters, and how their ports connect. Yosys `show` + netlistsvg
covers the gate-level case; we do not.

## Read first

- `README.md` — user-facing intro, CLI flags, examples for each format.
- Phase 1 bootstrap issue: [rtl-buddy/rtl-buddy-view#1](https://github.com/rtl-buddy/rtl-buddy-view/issues/1) (closed; scope shipped).
- Phase 2 (clock-domain overlay) issue: [rtl-buddy/rtl-buddy-view#2](https://github.com/rtl-buddy/rtl-buddy-view/issues/2) (closed; scope shipped).
- Phase 3 (reset-domain overlay) tracker: [rtl-buddy/rtl-buddy-view#3](https://github.com/rtl-buddy/rtl-buddy-view/issues/3). Unblocked — rtl-buddy-cdc#107 (analysis) and #108 (`--emit-reset-domain-map`) have shipped on cdc `main`.
- The integration meta-issue in rtl_buddy: [rtl-buddy/rtl_buddy#106](https://github.com/rtl-buddy/rtl_buddy/issues/106).
- The clock-domain map contract this tool consumes:
  [rtl-buddy/rtl-buddy-cdc#106](https://github.com/rtl-buddy/rtl-buddy-cdc/issues/106) — schema v1.0.

## Key files

```text
src/rtl_buddy_view/
├── __init__.py            # exposes main()
├── __main__.py            # `python -m rtl_buddy_view` entry
├── cli.py                 # Typer entry + orchestration
├── _filelist.py           # one-file-per-line filelist parser
├── _offsets.py            # UTF-8 byte-offset → (line, col) index
├── _verible_install.py    # vendored Verible fetch + binary discovery
├── _cst_cache.py          # content-hashed Verible JSON CST cache (XDG)
├── extractor.py           # frozen dataclasses: Module, Instance, Port, …
├── frontend/
│   ├── __init__.py        # Frontend enum + parse_to_modules() factory
│   ├── verible.py         # Verible CST walker → ModuleTable
│   └── slang.py           # slang frontend stub (NotImplementedError; #2 follow-up)
├── graph.py               # build_hierarchy(table, top) → HierNode tree
├── graph_export.py        # graph.json v1 (design tier); GRAPH_CONTRACT pinned
├── query.py               # walk, subtree, instances_of, port_connections, source_snippet
├── annotations.py         # DomainMap loader (rtl-buddy-cdc#106 schema v1.0)
└── render/
    ├── __init__.py
    ├── tree.py            # ASCII tree (clock + ⚠CDC markers)
    ├── dot.py             # Graphviz .dot (palette, edge labels, clock legend)
    ├── mermaid.py         # ```mermaid``` flowchart, GitHub-renderable
    └── json_render.py     # deterministic JSON; JSON_CONTRACT pinned

scripts/
└── fetch_verible.py       # convenience: install vendored Verible
tests/
├── fixtures/              # SV designs + paired files.f, plus a domain_map.json
│                          # (two_clock_design) for Phase 2 demos
└── test_*.py              # one per module + cross-cutting (smoke, clock overlay)
.github/workflows/
├── lint.yml               # ruff check + ruff format --check + mypy
└── test.yml               # pytest with Verible-cache step
```

## Development rules

- Keep changes targeted. The repo is small; resist sprawling
  refactors unless the task requires them.
- Treat the JSON output schema and the CLI surface as **public**.
  Downstream `rtl_buddy` parses the JSON and forwards CLI flags;
  breaking either ripples into the integration. The contract is
  pinned by `render.json_render.JSON_CONTRACT` +
  `tests/test_render_json.py::test_json_contract_keys_present_and_typed`.
- `__init__.py` stays minimal. Public modules are imported directly
  (`from rtl_buddy_view import extractor, graph, ...`); don't
  re-export symbols at the top level.
- Frozen dataclasses by default. Mutability is for parser-built
  collections only.
- The analyzer is a chain of **pure functions**. Side effects belong
  in `cli.py` (file I/O), the renderers (writing to a file-like),
  and the CST cache / Verible fetcher. Don't sneak I/O into
  `extractor.py` / `graph.py` / `query.py` / `annotations.py`.
- The frontend layer is the only module that subprocesses the
  Verible binary or imports `pyslang`. Everything downstream of
  `parse_to_modules()` works on the in-memory `ModuleTable` — no
  toolchain runtime dependency at the analyzer layer.
- No new top-level dependencies without strong reason. The package
  ships with **only `typer`** as a runtime dep; the slang frontend
  is gated behind the `[slang]` optional extra. Lint/test groups
  are the place for tooling.
- Output must be **deterministic**. All renderers sort their output
  (instance paths, edges, crossings) with alphabetical tie-breaks;
  the dot/mermaid clock palette is keyed by `sha256(clock_name)` so
  color choice is stable across `PYTHONHASHSEED`. Golden diffs
  downstream rely on this — don't introduce dict/set iteration
  order into emitted bytes.

## Validation commands

```bash
# from repo root
uv sync                        # set up env (Python ≥3.11; see pyproject)
uv run ruff check              # lint (must pass)
uv run ruff format --check     # format check (CI enforces this)
uv run mypy                    # type check (must pass; src/ scope only)
uv run pytest -q               # full unit suite + coverage gate
uv run pytest tests/test_<x>.py -q --no-cov   # single file, no gate

# end-to-end smoke
uv run rtl-buddy-view \
    --top counter_with_subs \
    --filelist tests/fixtures/counter_with_subs/files.f \
    --format tree

# clock-domain overlay
uv run rtl-buddy-view \
    --top two_clock_design \
    --filelist tests/fixtures/two_clock_design/files.f \
    --cdc-annotations tests/fixtures/two_clock_design/domain_map.json \
    --format dot --clock-legend
```

CI runs ruff + mypy (`lint.yml`) and pytest with coverage (`test.yml`)
on every PR. Run them locally before pushing.

### Coverage gate

`pytest` is wired to enforce `--cov-fail-under=80` aggregate across
the package (configured in `pyproject.toml`'s
`[tool.pytest.ini_options].addopts`). The per-file breakdown shows
in the pytest tail; drops in any single file surface at PR review.

To dodge the gate during exploratory work pass `--no-cov`. To
regenerate the report without re-running tests, use
`uv run coverage report -m`.

### Verible binary

The Verible-dependent tests skip when the binary isn't on PATH or in
`vendor/`. **CI fetches the pinned Verible release before pytest**
(`.github/workflows/test.yml` caches `vendor/verible/` keyed on the
hash of `_verible_install.py`). Locally, `uv run python scripts/fetch_verible.py`
(or `brew install verible` on macOS) is equivalent.

The pinned version + per-platform asset shapes live in
`src/rtl_buddy_view/_verible_install.py` (`PINNED_VERSION` +
`PLATFORM_ASSETS`). macOS and Linux ship different inner-directory
layouts — the `inner_dir_template` field encodes both. When bumping
the version, verify both platforms unpack correctly (CI is Linux;
test macOS locally) before merging.

### Editable install / live SPA rebuild

`rb hub start --serve-viewer` (in the sibling `rtl_buddy` repo)
loads the Vue SPA from `src/rtl_buddy_view/_viewer_bundle/` via
`importlib.resources`. For a **wheel install**, that directory is
baked in by `prebuild_viewer.py` at package time — users get a
served SPA without ever touching npm.

For an **editable install** (`uv pip install -e .` from this repo,
or a sibling checkout that `rtl_buddy` imports against), Python
resolves to *this* source tree at runtime — so the served SPA is
whatever is staged in `_viewer_bundle/` *right now*. Two
consequences:

1. `_viewer_bundle/` is **gitignored**. A `git pull` updates
   `viewer/src/**` but does **not** update the staged bundle —
   the hub will keep serving the old SPA until you re-stage.
2. After any pull that touches `viewer/`, or any local edit you
   want the hub to pick up, run:

```bash
npm --prefix viewer run build                            # Vite → viewer/dist/
uv run python scripts/prebuild_viewer.py --skip-npm      # stage → _viewer_bundle/
```

`--skip-npm` is fine when you just ran `npm run build` yourself;
omit it to let `prebuild_viewer.py` do the full `npm ci && npm run
build` from scratch. The stage step is idempotent.

Symptom of forgetting this step: a hard-refreshed viewer at
`http://127.0.0.1:<port>/` shows behaviour from a previous commit
even though `git log` says the fix is in. The browser cache is not
the culprit — the bundle on disk is stale.

## Adding a renderer

Renderers live under `src/rtl_buddy_view/render/` and follow a fixed
shape:

1. Define `render(node: HierNode, out: IO[str], *, domain_map: DomainMap | None = None, **fmt_specific) -> None`.
2. Walk the hierarchy deterministically — sort children at each level
   if you ever iterate in a way that affects output ordering.
3. Respect the graceful-degradation contract: when `domain_map is None`
   or `domain_map.is_empty`, output must be identical to the
   no-annotations case. The shared check is
   `active_map = domain_map if (domain_map and not domain_map.is_empty) else None`.
4. Wire into `cli.py`'s `OutputFormat` enum and `_render()` dispatch.
5. Add a `tests/test_render_<fmt>.py` with synthetic `HierNode`s
   (no Verible needed) for the format-specific decisions, plus
   coverage in `tests/test_clock_overlay.py` for the annotation
   path against the `two_clock_design` fixture.

## Adding a CLI flag

1. Add the `typer.Option` to `cli.main`. Document it inline in the
   `help=` string — that's what users see at `--help`.
2. If the flag changes JSON output, add a `JSON_CONTRACT` entry if
   the new field is downstream-public. Anything else can evolve
   freely.
3. If the flag is consumed by `rtl_buddy` (see § Cross-repo coupling),
   list it there and update the wrapper in the same change set.
4. Cover with a CLI integration test (see `tests/test_smoke.py` for
   the harness pattern).

## Cross-repo coupling

The `rtl_buddy` repo at `../rtl_buddy/` consumes this analyzer via
subprocess as `rb hier` — the wrapper is
[`tools/hier_rtl_buddy_view.py`](https://github.com/rtl-buddy/rtl_buddy/blob/main/src/rtl_buddy/tools/hier_rtl_buddy_view.py)
(meta-issue [rtl-buddy/rtl_buddy#106](https://github.com/rtl-buddy/rtl_buddy/issues/106)).
The contract:

- **CLI flags consumed today**: `--top`, `--filelist`, `--format`,
  `--output`, `--frontend`, `--cdc-annotations`, `--clock-legend`.
  Renaming or removing any of these will break the `rtl_buddy`
  wrapper.
- **`query` subcommands consumed by `rb hier-query`**
  ([rtl_buddy#198](https://github.com/rtl-buddy/rtl_buddy/issues/198)):
  `find-module`, `subtree`, `instances-of`, `port-connections`,
  `source-snippet`, each taking `--top`/`--filelist`/`--frontend`
  plus `subtree --format` and `source-snippet --context` /
  `--no-line-numbers`. JSON (or snippet text) on stdout; renaming a
  verb, an emitted JSON key, or an exit code breaks the wrapper the
  same way the render flags do.
- **JSON output schema** (pinned by `render.json_render.JSON_CONTRACT`
  + `tests/test_render_json.py::test_json_contract_keys_present_and_typed`):
  - `schema_version` (str) — currently `"1.0"`.
  - `tool.name` (str), `tool.version` (str).
  - `design.top` (str) — top module name.
  - `nodes` (list) — each entry carries `instance_path`,
    `module_name`, `instance_name`, `is_blackbox`,
    `param_overrides`, `port_connections`, `location`, `clock`,
    `crossings_in`. The `clock` / `crossings_in` keys are always
    present (`null` / `[]` when no domain map).
  - `edges` (list of `{parent, child}`) — instance-path pairs.
  Renaming or retyping any contract key is a CI failure.
- **`graph` subcommand** ([view#126](https://github.com/rtl-buddy/rtl-buddy-view/issues/126),
  epic [rtl_buddy#375](https://github.com/rtl-buddy/rtl_buddy/issues/375)):
  `--filelist`/`--top`/`--tb-top`/`--output`/`--frontend`/
  `--project-root`/`--meta`. Emits `graph.json` v1 — the **design
  tier** of the cross-repo knowledge graph, pinned by
  `graph_export.GRAPH_CONTRACT` + `schemas/graph-v1.json` +
  `tests/test_graph_export.py::test_graph_contract_keys_present_and_typed`,
  documented in `docs/graph-json-v1.md`. The node ids
  (`module:`, `inst:`, `port:`, `param:`, `iface:`, `modport:`) are
  the merge points rtl_buddy's config tier (rtl_buddy#376) and
  binding tier (rtl_buddy#378) attach their edges to, so a rename
  here breaks three repos, not one. Nothing volatile (test status,
  seeds, artefact paths) may be added to this payload — that's the
  results overlay's job.
- **Exit codes**: `0` = success, `1` = unresolved top / parse failure
  / filelist invalid / bad domain map (`AnnotationsError`),
  `2` = frontend not implemented (`NotImplementedError`, e.g. slang
  before Phase 2 activation). Match `rb cdc` exit-code conventions.
  The `graph` verb follows the same table.

When changing any of the above, update
`rtl_buddy/src/rtl_buddy/tools/hier_rtl_buddy_view.py` in the same
change set.

## Cross-repo coupling — hub design tokens (vendored FROM rtl_buddy)

Most cross-repo coupling in this file runs outward: rtl_buddy consumes
what we emit. The design-token sheet runs the other way, and it is the
mirror image of the hub-protocol schema (which *we* own and rtl_buddy
vendors).

- `viewer/src/theme.css` is a **byte-for-byte copy** of
  `src/rtl_buddy/hub/theme.css` in `../rtl_buddy/`. rtl_buddy owns it;
  a change here is a change made in the wrong repo.
- `tests/test_vendored_theme.py` guards it twice: a pinned sha256 (no
  sibling checkout needed — the one CI actually runs) and a byte-compare
  against `../rtl_buddy/` that **skips loudly** when that checkout has
  no sheet. The skip is deliberate: the sheet lands on rtl_buddy `main`
  with [rtl-buddy/rtl_buddy#398](https://github.com/rtl-buddy/rtl_buddy/issues/398).
- Updating it is a two-repo change set: land it there, copy the exact
  bytes here, re-pin `EXPECTED_SHA256` in the same commit.
- The SPA additionally implements rtl_buddy's **hub chrome contract**
  (top bar + bottom status strip, one status vocabulary). Both are
  documented in `docs/design-tokens.md`; the contract itself lives in
  rtl_buddy's `docs/concepts/hub.md`.
- **No hex literal belongs in a component's scoped `<style>`.** Colour
  decisions live in `theme.css` (shared), `tokens.css` (SPA-only) or
  `app.css` (shared classes) — see `docs/design-tokens.md`.

## Cross-repo coupling — clock-domain map (Phase 2)

This tool **consumes** the domain map emitted by rtl-buddy-cdc's
`--emit-domain-map` flag ([rtl-buddy-cdc#106](https://github.com/rtl-buddy/rtl-buddy-cdc/issues/106), schema v1.0). The
consumer-side contract is enforced in `annotations.py`:

- `SUPPORTED_SCHEMA_MAJOR = 1`. A mismatched major raises
  `AnnotationsError`. Minor-version drift is tolerated — new
  optional fields are ignored, missing optional fields default.
- The required top-level keys we read are `schema_version`,
  `generator_name`, `generator_version`, `design_top`,
  `design_frontend`; everything else is optional.
- `crossings[].async_per_sdc` (bool) is the gate for the `⚠CDC`
  marker — when `False`, the crossing is treated as a same-clock
  / synchronous artefact and not flagged.

When rtl-buddy-cdc bumps the schema, update both `annotations.py`'s
loader and `tests/fixtures/domain_maps/` in the same change set.

## Cross-repo coupling — hub protocol (this repo is source of truth)

`schemas/hub-protocol-v1.json` + `docs/hub-protocol.md` are the wire
contract for `rtl-buddy-hub`. rtl_buddy **vendors the schema file
byte-for-byte** at `src/rtl_buddy/hub/schema/hub-protocol-v1.json`, with
a byte-compare guard in its `tests/test_hub_protocol.py`. Consequences:

- Every schema edit is a lockstep two-repo change set: edit here,
  copy the exact bytes there, land both together. A reformat is a
  breaking change to the guard even when the JSON is equivalent.
- Additions stay on `v: 1`. The version bumps only when a `type` is
  removed or renamed — consumers already drop unknown `type`s at
  DEBUG (§11 of the doc), so a new event type is forward-compatible.
- Adding a peer means adding its `origin` in **eight** places (the
  envelope, `state_snapshot`'s three cached-state blocks + its peer
  list, `hello`'s envelope + `client`, `welcome`'s
  `registered_clients`). `tests/test_hub_protocol_schema.py`
  enum-sweeps for a half-updated copy and checks the doc's prose
  origin lists agree.
- A new pane gets its own origin, never a second `view` — the hub
  allows one client per origin, so sharing a slot makes two panes
  evict each other.

## Commit / branch / release conventions

- Commit messages: imperative subject, blank line, body that explains
  *why* not *what*. Co-author trailer
  `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`
  on assistant-authored commits.
- Branch off `main`. PRs land via squash-or-rebase; `main` is the
  release branch. Stacked PRs are fine — rebase the stack onto
  `origin/main` after each base merge to drop the cherry-picked
  commit (don't try to merge through the stack).
- Versioning: **derived from the git tag** by `hatch-vcs` at build
  time — there is no `[project].version` in `pyproject.toml`. Cut a
  release the same way rtl_buddy does: merge a PR carrying a
  `version/{patch,minor,major}` label and `.github/workflows/release.yml`
  computes the next semver, tags it, creates a GitHub Release, and
  builds + publishes the wheel to PyPI via Trusted Publishing. An
  unlabeled merged PR cuts no release; `workflow_dispatch` is the manual
  fallback. The JSON renderer reads the resulting version at runtime via
  `importlib.metadata.version("rtl-buddy-sch")` (falling back to the
  pre-rename `rtl-buddy-view` dist for old installs), so it flows through
  to the `tool.version` JSON field with no manual edit.
- **No CHANGELOG.** Release notes live on the GitHub Releases page
  (and in PR descriptions). A `CHANGELOG.md` was deliberately not
  introduced — it serializes every merge through one file and
  triggers conflicts on parallel PRs.
