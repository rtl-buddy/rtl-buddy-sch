# AGENTS.md — rtl-buddy-view

## Role

This repo is the source-of-truth implementation of the `rtl-buddy-view`
analyzer — a Python tool that extracts the module-level hierarchy and
connectivity of a SystemVerilog design and renders it as ASCII tree /
Graphviz `.dot` / Mermaid / JSON. Comments, parameter overrides, and
source positions flow through every layer.

It is consumed by `rtl_buddy` (sibling repo) as a subprocess via
`rb hier` (see [rtl-buddy/rtl_buddy#106](https://github.com/rtl-buddy/rtl_buddy/issues/106)).
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
- Phase 3 (reset-domain overlay) tracker: [rtl-buddy/rtl-buddy-view#3](https://github.com/rtl-buddy/rtl-buddy-view/issues/3) (blocked on rtl-buddy-cdc#107 / #108).
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
subprocess (`rb hier`, tracked at [rtl-buddy/rtl_buddy#106](https://github.com/rtl-buddy/rtl_buddy/issues/106)).
The contract:

- **CLI flags consumed today**: `--top`, `--filelist`, `--format`,
  `--output`, `--frontend`, `--cdc-annotations`, `--clock-legend`.
  Renaming or removing any of these will break the `rtl_buddy`
  wrapper.
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
- **Exit codes**: `0` = success, `1` = unresolved top / parse failure
  / filelist invalid / bad domain map (`AnnotationsError`),
  `2` = frontend not implemented (`NotImplementedError`, e.g. slang
  before Phase 2 activation). Match `rb cdc` exit-code conventions.

When changing any of the above, update
`rtl_buddy/src/rtl_buddy/tools/hier_rtl_buddy_view.py` in the same
change set.

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

## Commit / branch / release conventions

- Commit messages: imperative subject, blank line, body that explains
  *why* not *what*. Co-author trailer
  `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`
  on assistant-authored commits.
- Branch off `main`. PRs land via squash-or-rebase; `main` is the
  release branch. Stacked PRs are fine — rebase the stack onto
  `origin/main` after each base merge to drop the cherry-picked
  commit (don't try to merge through the stack).
- Versioning: bump `pyproject.toml` `[project].version` when cutting
  a release. The JSON renderer reads it at runtime via
  `importlib.metadata.version("rtl-buddy-view")`, so a single bump
  flows through to the `tool.version` JSON field.
- **No CHANGELOG.** Release notes live on the GitHub Releases page
  (and in PR descriptions). A `CHANGELOG.md` was deliberately not
  introduced — it serializes every merge through one file and
  triggers conflicts on parallel PRs.
