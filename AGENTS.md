# AGENTS.md — rtl-buddy-view

## Role

This repo is the source-of-truth implementation of the `rtl-buddy-view`
analyzer — a Python-based tool that extracts the module-level
hierarchy and connectivity of a SystemVerilog design and renders it
as Graphviz / ASCII tree / Mermaid / JSON. Comments, parameter
overrides, and source positions are preserved end-to-end.

It is consumed by `rtl_buddy` (sibling repo) as a subprocess via
`rb hier` (see [rtl-buddy/rtl_buddy#106](https://github.com/rtl-buddy/rtl_buddy/issues/106)).
Anything that breaks the JSON output schema or the CLI surface is a
downstream-breaking change — see [§ Cross-repo coupling](#cross-repo-coupling).

This is **not** a netlist/schematic visualizer. The target is the
*module-level* structural view: what instantiates what, with which
parameters, and how their ports connect. Yosys `show` + netlistsvg
covers the gate-level case; we do not.

## Read first

- `README.md` — user-facing intro, CLI flags, current Phase 1 scope.
- The Phase 1 bootstrap issue: [rtl-buddy/rtl-buddy-view#1](https://github.com/rtl-buddy/rtl-buddy-view/issues/1)
- The integration meta-issue in rtl_buddy: [rtl-buddy/rtl_buddy#106](https://github.com/rtl-buddy/rtl_buddy/issues/106)

## Key files

```text
src/rtl_buddy_view/
├── __init__.py         # exposes main()
├── cli.py              # Typer entry points + orchestration
├── frontend/
│   ├── __init__.py     # Frontend enum + elaborate() factory
│   ├── verible.py      # Verible frontend: subprocess + JSON CST cache
│   └── slang.py        # slang frontend (Phase 2 fallback for generates / parameters)
├── extractor.py        # SV-semantic dataclasses + CST → Module walker
├── graph.py            # Hierarchy graph builder (instance tree/DAG)
├── render/
│   ├── __init__.py
│   ├── tree.py         # ASCII tree renderer
│   └── dot.py          # Graphviz .dot renderer
└── query.py            # Query API surface (find_module, subtree, …)
tests/
├── fixtures/           # paired SV designs + golden tree/dot outputs
└── test_*.py
.github/workflows/
├── lint.yml            # ruff check + ruff format --check + mypy
└── test.yml            # pytest
```

## Development rules

- Keep changes targeted. The repo is small; resist sprawling
  refactors unless the task requires them.
- Treat the JSON output schema and the CLI surface as **public**.
  Downstream `rtl_buddy` will parse the JSON and forward CLI flags;
  breaking either ripples into the integration.
- `__init__.py` stays minimal. Public modules are imported directly
  (`from rtl_buddy_view import extractor, graph, ...`); don't
  re-export symbols at the top level.
- Frozen dataclasses by default. Mutability is for parser-built
  collections only.
- The analyzer is a chain of **pure functions**. Side effects belong
  in `cli.py` (file I/O) and the renderers (writing to a file-like).
  Don't sneak I/O into `extractor.py` / `graph.py` / `query.py`.
- The frontend layer is the only module that subprocesses the
  Verible binary or imports `pyslang`. Everything downstream of
  `extractor.py` works on the in-memory `Module` dataclass — no
  toolchain runtime dependency at the analyzer layer.
- No new top-level dependencies without strong reason. The package
  ships with **only `typer`** as a runtime dep; the slang frontend
  is gated behind the `[slang]` optional extra.

## Validation commands

```bash
# from repo root
uv sync                        # set up env (Python 3.13; see .python-version)
uv run ruff check              # lint (must pass)
uv run ruff format --check     # format check (CI enforces this)
uv run mypy                    # type check (must pass; src/ scope only)
uv run pytest -q               # full unit suite + coverage gate
uv run pytest tests/test_<x>.py -q --no-cov   # single file, no gate

# end-to-end smoke
uv run rtl-buddy-view --top counter --filelist tests/fixtures/counter/counter.f \
    --format tree
```

CI runs ruff + mypy (`lint.yml`) and pytest with coverage (`test.yml`) on
every PR. Run them locally before pushing.

### Coverage gate

`pytest` is wired to enforce `--cov-fail-under=80` aggregate across
the package (configured in `pyproject.toml`'s
`[tool.pytest.ini_options].addopts`). The Phase 1 acceptance
criterion (issue #1) calls for ≥80% on `frontend/verible.py`,
`extractor.py`, and `graph.py` — the aggregate gate enforces a
slightly weaker but practical equivalent. The per-file breakdown
shows in the pytest tail; drops in any single file surface at PR
review.

To dodge the gate during exploratory work pass `--no-cov`. To
regenerate the report without re-running tests, use
`uv run coverage report -m`.

The verible-dependent integration tests skip when the binary isn't
on PATH or in `vendor/`. **CI fetches the pinned Verible release
before running pytest** so coverage stays meaningful; locally,
`uv run python scripts/fetch_verible.py` (or `brew install verible`)
is the same.

## Cross-repo coupling

The `rtl_buddy` repo at `../rtl_buddy/` will consume this analyzer
via subprocess (`rb hier`, tracked at [rtl-buddy/rtl_buddy#106](https://github.com/rtl-buddy/rtl_buddy/issues/106)).
The contract once integration lands:

- **CLI flags to be consumed**: `--top`, `--filelist`, `--format`,
  `--output`. Renaming or removing any of these will break the
  `rtl_buddy` wrapper. Pin them before the first wrapper PR lands.
- **JSON output schema**: TBD when the `json` renderer is added in
  Phase 2. Will follow the rtl-buddy-cdc pattern (`reporter.JSON_CONTRACT`
  + a `test_json_contract_keys_are_stable` test) so a rename or
  retype is a CI failure.
- **Exit codes**: 0 = success, 1 = unresolved top / parse failure
  (top not found, filelist invalid). Match `rb cdc` exit-code
  conventions.

When changing any of the above, update
`rtl_buddy/src/rtl_buddy/tools/hier_rtl_buddy_view.py` in the same
change set.

## Commit / branch / release conventions

- Commit messages: imperative subject, blank line, body that explains
  *why* not *what*. Co-author trailer
  `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`
  on assistant-authored commits.
- Branch off `main`. PRs land via squash-or-rebase; `main` is the
  release branch.
- Versioning: bump `pyproject.toml` `[project].version` when cutting
  a release. Record in `CHANGELOG.md` (Keep a Changelog format) once
  v0.1 ships.
