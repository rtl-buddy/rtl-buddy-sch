"""Typer CLI for ``rtl-buddy-view``.

Phase 4 (#17) surface::

    rtl-buddy-view --top <module> --filelist <file> \\
                   [--format tree|dot|mermaid|json] [--output <path>] \\
                   [--frontend verible|slang] \\
                   [--overlay name=path]... \\
                   [--list-overlays] \\
                   [--clock-legend]

``--overlay name=path`` is the single generalized hook for every
overlay type — clock + reset built-ins ship today, coverage / phys
/ wave land in later phases as plugins. The flag is repeatable;
loaders dispatch through :class:`rtl_buddy_view.overlays.OverlayRegistry`.

The pre-#17 ``--cdc-annotations`` / ``--rdc-annotations`` flags
stay as deprecated aliases: when set, they emit a stderr warning
and rewrite internally to ``--overlay clock=…`` / ``--overlay
reset=…``. They'll be removed in the next major bump.

Two subcommands sit alongside the render callback::

    rtl-buddy-view query <verb> …    # JSON answers over the hierarchy
    rtl-buddy-view graph -f <file> --top <module> -o graph.json

``graph`` (#126) exports the design tier of the knowledge graph —
a different artifact from ``--format json``, see
:mod:`rtl_buddy_view.graph_export`.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from enum import Enum
from pathlib import Path
from typing import IO, Annotated

import typer

from rtl_buddy_view._filelist import FilelistError, parse_filelist
from rtl_buddy_view.axi_perf_annotations import (
    AxiPerfAnnotationsError,
    AxiPerfMap,
)
from rtl_buddy_view.annotations import (
    AnnotationsError,
    DomainMap,
)
from rtl_buddy_view.coverage_annotations import (
    DEFAULT_URL_BASE as COVERAGE_DEFAULT_URL_BASE,
    CoverageAnnotationsError,
    CoverageMap,
)
from rtl_buddy_view import graph_export, query
from rtl_buddy_view.extractor import ModuleTable
from rtl_buddy_view import elk_export
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.frontend.verible import VeribleParseError, VeribleUnavailable
from rtl_buddy_view.graph import (
    HierarchyError,
    HierNode,
    build_hierarchy,
    find_tb_top,
)
from rtl_buddy_view.hints import (
    HintMap,
    HintsError,
    apply_hints,
    merge_hint_maps,
    resolve_hints,
    scan_pragmas,
)
from rtl_buddy_view.overlays import OverlayError, OverlayRegistry, default_registry
from rtl_buddy_view.render import dot as dot_render
from rtl_buddy_view.render import json_render
from rtl_buddy_view.render import mermaid as mermaid_render
from rtl_buddy_view.render import tree as tree_render
from rtl_buddy_view.reset_annotations import (
    ResetAnnotationsError,
    ResetDomainMap,
)
from rtl_buddy_view.wave_annotations import (
    WaveAnnotationsError,
    WaveMap,
)

app = typer.Typer(
    help="RTL hierarchy and connectivity visualization.",
    no_args_is_help=True,
)


class OutputFormat(str, Enum):
    tree = "tree"
    dot = "dot"
    mermaid = "mermaid"
    json = "json"
    elk = "elk"


class CoverageMetric(str, Enum):
    """Which coverage channel drives the viewer's heatmap tint."""

    lines = "lines"
    branches = "branches"
    toggles = "toggles"


def _version_callback(value: bool) -> None:
    """Print ``rtl-buddy-view <X.Y.Z>`` and exit, for ``--version``.

    Eager so it fires before the required-option validation in
    ``main()``. Downstream consumers (rtl_buddy's tool_manifest)
    probe this to enforce a version floor, so the format is a
    contract: the literal ``rtl-buddy-view`` followed by the
    importlib.metadata version string. The literal survives the
    distribution rename to ``rtl-buddy-sch`` — released rtl_buddy
    matches ``rtl-buddy-view\\s+<ver>`` and must keep parsing.
    """
    if not value:
        return
    import rtl_buddy_view

    typer.echo(f"rtl-buddy-view {rtl_buddy_view.__version__}")
    raise typer.Exit(code=0)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print the rtl-buddy-view version and exit.",
    ),
    top: str = typer.Option(
        None,
        "--top",
        "-t",
        help="DUT top module name. When --tb-top is also set, the "
        "renderer elaborates from --tb-top and records this name in "
        "view.json::dut_top so the SPA can mark every DUT instance. "
        "When --tb-top is unset, the renderer elaborates from --top "
        "(today's behaviour). At least one of --top or --tb-top is "
        "required.",
    ),
    tb_top: str = typer.Option(
        None,
        "--tb-top",
        help="Testbench top module name. Independent of --top: when "
        "set on its own, the renderer elaborates from this module and "
        "records view.json::tb_top; overlays (CDC, AXI perf, ...) "
        "still anchor under their own design_top at load time by "
        "walking nodes[] for module-name matches. Combine with --top "
        "to record both anchors.",
    ),
    filelist: Path | None = typer.Option(
        None,
        "--filelist",
        "-f",
        exists=True,
        readable=True,
        help="Path to a filelist (one source file per line; "
        "rtl-buddy filelist conventions to be honored once the "
        "Verible frontend lands — see issue #1).",
    ),
    output_format: OutputFormat = typer.Option(
        OutputFormat.tree,
        "--format",
        case_sensitive=False,
        help="Output format. tree = ASCII; dot = Graphviz; "
        "mermaid = markdown-embeddable flowchart; json = "
        "machine-readable (rtl_buddy consumes this); elk = "
        "engine-neutral ELK-shaped schematic payload with ports, "
        "pins and bus widths, for an elkjs canvas (docs/elk-json-v1.md).",
    ),
    output_path: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write the rendered diagram to this file (default: stdout).",
    ),
    frontend: Frontend = typer.Option(
        Frontend.verible,
        "--frontend",
        case_sensitive=False,
        help="Parser frontend. verible (default) operates on the source "
        "CST; slang (Phase 2) is the elaborated fallback.",
    ),
    overlays_flag: list[str] = typer.Option(
        [],
        "--overlay",
        help="Overlay annotation in the form name=path "
        "(repeatable). Examples: --overlay clock=clock-map.json, "
        "--overlay reset=reset-map.json. Run --list-overlays for "
        "the names this binary supports.",
    ),
    list_overlays: bool = typer.Option(
        False,
        "--list-overlays",
        help="Print the registered overlays + their schema versions and exit.",
    ),
    cdc_annotations: Path | None = typer.Option(
        None,
        "--cdc-annotations",
        exists=True,
        readable=True,
        help="(Deprecated; use --overlay clock=PATH.) Clock-domain map "
        "JSON from `rtl-buddy-cdc --emit-domain-map`.",
        hidden=True,
    ),
    rdc_annotations: Path | None = typer.Option(
        None,
        "--rdc-annotations",
        exists=True,
        readable=True,
        help="(Deprecated; use --overlay reset=PATH.) Reset-domain map "
        "JSON from `rtl-buddy-cdc --emit-reset-domain-map`.",
        hidden=True,
    ),
    coverage_metric: CoverageMetric = typer.Option(
        CoverageMetric.lines,
        "--coverage-metric",
        case_sensitive=False,
        help="Coverage overlay only: which channel (lines, branches, "
        "toggles) drives the heatmap tint. Recorded in "
        "view.json::overlay_meta.coverage.metric for the web viewer. "
        "No effect without --overlay coverage=PATH.",
    ),
    coverage_url_base: str = typer.Option(
        COVERAGE_DEFAULT_URL_BASE,
        "--coverage-url-base",
        help="Coverage overlay only: Coverview server base URL used "
        "to build per-node deep links (default: the Coverview dev "
        "server address).",
    ),
    clock_legend: bool = typer.Option(
        False,
        "--clock-legend",
        help="Dot format only: emit a side legend listing each clock "
        "with its assigned color swatch. No effect without a clock overlay.",
    ),
    block_diagram: bool = typer.Option(
        False,
        "--block-diagram",
        help="Dot format only: render a documentation block diagram "
        "instead of the instantiation tree. Nesting becomes "
        "containment (each non-leaf instance is its own box) and the "
        "arrows show sibling-to-sibling dataflow — which block drives "
        "which net — rather than parent-to-child port maps. Ignored "
        "with a warning for other formats.",
    ),
    no_pragmas: bool = typer.Option(
        False,
        "--no-pragmas",
        help="Ignore in-source '// rbsch:' diagram pragmas (leaf, "
        "collapse, hide, label=…). They are scanned automatically "
        "from the filelist's sources and rewrite the hierarchy for "
        "every format; this is the escape hatch for seeing the "
        "design as written. Does not affect --overlay hints=PATH.",
    ),
) -> None:
    """Render the hierarchy of ``--top`` to ``--format``."""
    # A subcommand (``query``) is being dispatched — the render
    # options on this callback are unused defaults, so the required-
    # option validation below must not fire.
    if ctx.invoked_subcommand is not None:
        return

    registry = default_registry()

    if list_overlays:
        for overlay in registry:
            source = registry.source_of(overlay.name)
            typer.echo(f"{overlay.name}\t{overlay.schema_version}\t({source})")
        raise typer.Exit(code=0)

    # `--filelist` and at least one of `--top` / `--tb-top` are
    # mandatory for actual rendering, but `--list-overlays` is a pure
    # diagnostic so we accept the invocation without them. Validate
    # here instead of marking the options as required at the typer
    # level.
    if filelist is None:
        typer.echo("error: --filelist is required", err=True)
        raise typer.Exit(code=2)
    if top is None and tb_top is None:
        typer.echo("error: --top or --tb-top is required", err=True)
        raise typer.Exit(code=2)

    if block_diagram and output_format is not OutputFormat.dot:
        # Additive flag, so an ignored value must never be silent —
        # a user asking for a block diagram and getting a tree back
        # deserves to know which half of the request was dropped.
        typer.echo(
            f"warning: --block-diagram applies to --format dot only "
            f"(got {output_format.value}); ignoring",
            err=True,
        )
        block_diagram = False

    overlay_specs = _collect_overlays(overlays_flag, cdc_annotations, rdc_annotations)

    annotations: dict[str, object] = {}
    for name, path in overlay_specs:
        try:
            overlay = registry.get(name)
        except OverlayError as e:
            typer.echo(f"overlay: {e}", err=True)
            raise typer.Exit(code=1) from None
        try:
            annotations[name] = overlay.load(path)
        except (
            AnnotationsError,
            ResetAnnotationsError,
            AxiPerfAnnotationsError,
            WaveAnnotationsError,
            CoverageAnnotationsError,
            HintsError,
        ) as e:
            # Loader exceptions carry the overlay's own prefix in
            # their message; we just qualify with the overlay name
            # here so the user sees which artefact failed when
            # multiple --overlay flags were supplied.
            typer.echo(f"overlay {name}: {e}", err=True)
            raise typer.Exit(code=1) from None

    table = _parse_design_or_exit(filelist, frontend)

    # (The option is declared ``str`` with a ``None`` default — the
    # typer convention here — so narrow before rebinding.)
    detected_tb_top = _resolve_tb_top(table, top, tb_top)
    if detected_tb_top is not None:
        tb_top = detected_tb_top

    # The rendered root is whatever ``--tb-top`` resolved to; when only
    # --top is supplied, fall back to DUT-rooted (byte-identical to the
    # pre-tb-top behaviour). Both names survive into view.json as
    # descriptive fields regardless of which was used as the root.
    rendered_top = tb_top if tb_top is not None else top

    root = _build_hierarchy_or_exit(table, rendered_top)

    # Diagram hints (epic #159) rewrite the *graph*, not the render,
    # so they apply once here and every format sees the same design.
    # In-source pragmas are the default input (the sources are
    # already named by the filelist); the sidecar overlay is the
    # override for IP whose source can't carry comments.
    root = _apply_hints_or_warn(
        root,
        table,
        filelist,
        no_pragmas=no_pragmas,
        sidecar=annotations.get("hints"),
    )

    domain_map: DomainMap | None = annotations.get("clock")  # type: ignore[assignment]
    reset_map: ResetDomainMap | None = annotations.get("reset")  # type: ignore[assignment]
    axi_perf_map: AxiPerfMap | None = annotations.get("axi-perf")  # type: ignore[assignment]
    wave_map: WaveMap | None = annotations.get("wave")  # type: ignore[assignment]
    coverage_map: CoverageMap | None = annotations.get("coverage")  # type: ignore[assignment]

    # The overlay protocol's load(path) has no channel for CLI knobs,
    # so the Coverview URL base is assigned post-load. Deep links in
    # view.json and the overlay_meta block both read it from the map.
    if coverage_map is not None:
        coverage_map.url_base = coverage_url_base

    # Phase 6e (#99): TB-context clock + reset map. When a
    # ``tb_clock_map.json`` is loaded via ``--overlay clock-tb=…``,
    # merge its TB-side clock + reset entries into the DUT-side
    # maps. DUT-side wins inside every DUT instance (the rule pinned
    # by #99's TB-overlay story); TB-side fills outside. The renderer
    # then sees one unified DomainMap / ResetDomainMap, so existing
    # per-node clock/reset contribution code paths apply unchanged.
    from rtl_buddy_view.tb_clock_map import (
        TbClockMap,
        merge_into_domain_map,
        merge_into_reset_map,
    )

    tb_clock_map_raw = annotations.get("clock-tb")
    tb_clock_map: TbClockMap | None = (
        tb_clock_map_raw if isinstance(tb_clock_map_raw, TbClockMap) else None
    )
    if tb_clock_map is not None:
        domain_map = merge_into_domain_map(
            domain_map,
            tb_clock_map,
            root=root,
            dut_top_module=top,
            warn_stream=sys.stderr,
        )
        if tb_clock_map.resets:
            reset_map = merge_into_reset_map(
                reset_map,
                tb_clock_map,
                root=root,
                dut_top_module=top,
                warn_stream=sys.stderr,
            )
    # Carry the original axi-perf.json path through to the JSON
    # renderer so view.json can carry a top-level `axi_perf.source`
    # field — the SPA's "Open in marimo" button reads it to skip the
    # test/suite_dir prompts (Phase 2.5 of the marimo umbrella).
    axi_perf_source: Path | None = None
    for name, path in overlay_specs:
        if name == "axi-perf":
            axi_perf_source = path
            break

    sink: IO[str]
    if output_path is None:
        sink = sys.stdout
        _render(
            root,
            output_format,
            sink,
            domain_map,
            reset_map,
            axi_perf_map,
            wave_map,
            clock_legend,
            top,
            tb_top,
            axi_perf_source=axi_perf_source,
            module_table=table,
            coverage_map=coverage_map,
            coverage_metric=coverage_metric.value,
            block_diagram=block_diagram,
        )
    else:
        with output_path.open("w") as sink:
            _render(
                root,
                output_format,
                sink,
                domain_map,
                reset_map,
                axi_perf_map,
                wave_map,
                clock_legend,
                top,
                tb_top,
                axi_perf_source=axi_perf_source,
                module_table=table,
                coverage_map=coverage_map,
                coverage_metric=coverage_metric.value,
                block_diagram=block_diagram,
            )


def _collect_overlays(
    overlay_args: list[str],
    cdc_alias: Path | None,
    rdc_alias: Path | None,
) -> list[tuple[str, Path]]:
    """Parse + canonicalise the user's overlay specs.

    Resolves the two deprecated aliases (``--cdc-annotations`` /
    ``--rdc-annotations``) into the new ``--overlay name=path`` form
    with a stderr deprecation warning each. Rejects malformed
    ``name=path`` and duplicate names in the same invocation.
    """
    specs: list[tuple[str, Path]] = []
    seen: set[str] = set()

    def _add(name: str, path: Path, *, source_label: str) -> None:
        if name in seen:
            typer.echo(
                f"overlay: duplicate overlay {name!r} "
                f"(supplied via {source_label}); each overlay may "
                f"be passed at most once per invocation",
                err=True,
            )
            raise typer.Exit(code=2)
        seen.add(name)
        specs.append((name, path))

    if cdc_alias is not None:
        typer.echo(
            "warning: --cdc-annotations is deprecated; "
            "use --overlay clock=PATH instead.",
            err=True,
        )
        _add("clock", cdc_alias, source_label="--cdc-annotations")

    if rdc_alias is not None:
        typer.echo(
            "warning: --rdc-annotations is deprecated; "
            "use --overlay reset=PATH instead.",
            err=True,
        )
        _add("reset", rdc_alias, source_label="--rdc-annotations")

    for spec in overlay_args:
        if "=" not in spec:
            typer.echo(
                f"overlay: malformed spec {spec!r} — expected name=path",
                err=True,
            )
            raise typer.Exit(code=2)
        name, _, raw_path = spec.partition("=")
        name = name.strip()
        raw_path = raw_path.strip()
        if not name or not raw_path:
            typer.echo(
                f"overlay: malformed spec {spec!r} — name and path both required",
                err=True,
            )
            raise typer.Exit(code=2)
        path = Path(raw_path)
        # The ``wave`` overlay's path argument carries an optional
        # ``:<time_spec>`` suffix (``foo.vcd:12500ns``), so the
        # literal path string won't exist as a file. Defer the
        # existence check to the wave loader, which strips the time
        # suffix before opening the VCD.
        if name != "wave" and not path.exists():
            typer.echo(
                f"overlay {name}: file not found: {path}",
                err=True,
            )
            raise typer.Exit(code=1)
        _add(name, path, source_label=f"--overlay {name}=…")

    return specs


def _render(
    root,
    fmt: OutputFormat,
    sink: IO[str],
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None,
    axi_perf_map: AxiPerfMap | None,
    wave_map: WaveMap | None,
    clock_legend: bool,
    dut_top: str | None,
    tb_top: str | None,
    *,
    axi_perf_source: Path | None = None,
    module_table: ModuleTable | None = None,
    coverage_map: CoverageMap | None = None,
    coverage_metric: str = "lines",
    block_diagram: bool = False,
) -> None:
    # The coverage overlay contributes to view.json only (the web
    # viewer paints the heatmap); tree/dot/mermaid output is
    # byte-identical with or without it.
    if fmt is OutputFormat.tree:
        tree_render.render(root, sink, domain_map=domain_map, reset_map=reset_map)
    elif fmt is OutputFormat.dot:
        dot_render.render(
            root,
            sink,
            domain_map=domain_map,
            reset_map=reset_map,
            axi_perf_map=axi_perf_map,
            with_legend=clock_legend,
            block_diagram=block_diagram,
            module_table=module_table,
        )
    elif fmt is OutputFormat.mermaid:
        mermaid_render.render(root, sink, domain_map=domain_map, reset_map=reset_map)
    elif fmt is OutputFormat.elk:
        # The exporter reads formal pin names and declared widths off
        # the module table, so an absent table is not a degraded
        # payload — it is an empty one. The CLI always supplies it.
        elk_export.render(
            root,
            sink,
            module_table=module_table if module_table is not None else ModuleTable(),
            domain_map=domain_map,
        )
    elif fmt is OutputFormat.json:
        json_render.render(
            root,
            sink,
            domain_map=domain_map,
            reset_map=reset_map,
            axi_perf_map=axi_perf_map,
            wave_map=wave_map,
            coverage_map=coverage_map,
            coverage_metric=coverage_metric,
            axi_perf_source=axi_perf_source,
            with_legend=clock_legend,
            module_table=module_table,
            dut_top=dut_top,
            tb_top=tb_top,
        )
    else:  # pragma: no cover
        raise ValueError(f"Unknown format: {fmt}")


def _apply_hints_or_warn(
    root: HierNode,
    table: ModuleTable,
    filelist: Path,
    *,
    no_pragmas: bool,
    sidecar: object,
) -> HierNode:
    """Scan pragmas, merge the sidecar, and rewrite the hierarchy.

    The I/O half of the hint layer: :mod:`rtl_buddy_view.hints` never
    opens a file for the in-source path, so the sources are read here
    and keyed by the same resolved path string the frontend recorded
    in every ``SourceLocation`` — that string equality *is* the
    association, so it must not be re-derived or normalised.

    Unreadable sources are skipped rather than fatal: a file the
    frontend already parsed but that we can't re-read is a hint-layer
    problem, and losing a hint should never lose the diagram.
    """
    pragma_hints = HintMap()
    if not no_pragmas:
        sources: dict[str, str] = {}
        try:
            files = parse_filelist(filelist)
        except FilelistError:  # pragma: no cover - main() already validated
            files = []
        for path in files:
            try:
                sources[str(path)] = path.read_text()
            except OSError:  # pragma: no cover - raced/permission-denied source
                continue
        pragma_hints = resolve_hints(scan_pragmas(sources), table)

    hints = pragma_hints
    if isinstance(sidecar, HintMap):
        # Sidecar over pragma: pointing at a file on the command line
        # is the more deliberate act, and it's the only lever available
        # for source the author can't edit.
        hints = merge_hint_maps(pragma_hints, sidecar)

    warnings: list[str] = []
    hinted = apply_hints(root, hints, warnings=warnings)
    for message in list(hints.warnings) + warnings:
        typer.echo(f"hints: {message}", err=True)
    return hinted


def _parse_design_or_exit(filelist: Path, frontend: Frontend) -> ModuleTable:
    """Filelist → ModuleTable with the CLI's error/exit-code contract.

    Shared by the render path (``main``) and every ``query``
    subcommand so both surfaces fail with identical stderr text and
    exit codes: 1 for filelist/parse errors, 2 for a frontend that
    isn't implemented.
    """
    try:
        files = parse_filelist(filelist)
    except FilelistError as e:
        typer.echo(f"filelist: {e}", err=True)
        raise typer.Exit(code=1) from None

    try:
        return parse_to_modules(files, frontend=frontend)
    except (VeribleUnavailable, VeribleParseError) as e:
        typer.echo(f"verible: {e}", err=True)
        raise typer.Exit(code=1) from None
    except NotImplementedError as e:
        typer.echo(f"frontend: {e}", err=True)
        raise typer.Exit(code=2) from None


def _resolve_tb_top(
    table: ModuleTable, top: str | None, tb_top: str | None
) -> str | None:
    """Fix up a ``--tb-top`` hint that doesn't name a real module.

    The testbench *config* name (e.g. ``tb_apb``) frequently differs
    from the actual top module (commonly just ``tb_top``), so a
    best-effort hint should still produce a TB-rooted result instead
    of a "module not found" error. Recovers the real top from the
    elaborated design; returns the hint unchanged when it already
    resolves or when detection is ambiguous.

    Shared by the render path and ``graph`` so both surfaces accept
    the same sloppy hint with the same stderr note.
    """
    if tb_top is None or top is None or tb_top in table.modules_by_name:
        return tb_top
    detected = find_tb_top(table, top)
    if detected is None:
        return tb_top
    typer.echo(
        f"tb-top: {tb_top!r} is not a module in the design; "
        f"auto-detected {detected!r} as the testbench top.",
        err=True,
    )
    return detected


def _build_hierarchy_or_exit(table: ModuleTable, top: str) -> HierNode:
    try:
        return build_hierarchy(table, top)
    except HierarchyError as e:
        typer.echo(f"hierarchy: {e}", err=True)
        raise typer.Exit(code=1) from None


# --- `query` subcommands (#198 in rtl_buddy) --------------------------------
#
# Thin CLI over :mod:`rtl_buddy_view.query` so shell pipelines, agents,
# and non-Python tooling can hit the same surface Python consumers get
# from ``import rtl_buddy_view.query`` — without a Python harness.
# Output is JSON on stdout (``source-snippet`` emits plain text: its
# whole point is the line-number-prefixed citation format).

query_app = typer.Typer(
    help="Query the elaborated hierarchy: JSON answers on stdout, "
    "for shell pipelines and agent tool use. Thin wrapper over the "
    "rtl_buddy_view.query Python API.",
    no_args_is_help=True,
)
app.add_typer(query_app, name="query")


_QueryTop = Annotated[
    str,
    typer.Option(
        "--top",
        "-t",
        help="Top module the queried hierarchy is rooted at.",
    ),
]
_QueryFilelist = Annotated[
    Path,
    typer.Option(
        "--filelist",
        "-f",
        exists=True,
        readable=True,
        help="Path to a filelist (one source file per line).",
    ),
]
_QueryFrontend = Annotated[
    Frontend,
    typer.Option(
        "--frontend",
        case_sensitive=False,
        help="Parser frontend (verible|slang).",
    ),
]


class QueryFormat(str, Enum):
    json = "json"
    tree = "tree"


def _location_payload(node: HierNode) -> dict | None:
    """The module-definition anchor for ``node`` (None for blackboxes)."""
    if node.module is None or node.module.location is None:
        return None
    return asdict(node.module.location)


def _node_payload(node: HierNode, *, with_children: bool = True) -> dict:
    """JSON projection of one :class:`HierNode`.

    Mirrors the per-node field names of view.json v1 (instance_path,
    module_name, instance_name, is_blackbox, param_overrides,
    location) so consumers that already read ``rb hier --format
    json`` output don't learn a second vocabulary. ``children`` is
    nested recursively for ``subtree``; ``instances-of`` omits it.
    """
    payload: dict = {
        "instance_path": node.instance_path,
        "module_name": node.module_name,
        "instance_name": node.instance.name if node.instance is not None else None,
        "is_blackbox": node.is_blackbox,
        "param_overrides": [asdict(po) for po in node.instance.param_overrides]
        if node.instance is not None
        else [],
        "location": _location_payload(node),
    }
    if with_children:
        payload["children"] = [_node_payload(c) for c in node.children]
    return payload


def _emit_json(payload: object) -> None:
    typer.echo(json.dumps(payload, indent=2))


def _subtree_or_exit(root: HierNode, instance_path: str) -> HierNode:
    node = query.subtree(root, instance_path)
    if node is None:
        typer.echo(
            f"query: instance path {instance_path!r} not found "
            f"(hierarchy is rooted at {root.instance_path!r})",
            err=True,
        )
        raise typer.Exit(code=1)
    return node


@query_app.command("find-module")
def query_find_module(
    name: Annotated[str, typer.Argument(help="Module name to look up.")],
    top: _QueryTop,
    filelist: _QueryFilelist,
    frontend: _QueryFrontend = Frontend.verible,
) -> None:
    """Print the module definition (ports, parameters, instances) as JSON.

    Exits 1 with a message on stderr when the module is not defined
    in the filelist's sources. ``--top`` is accepted for surface
    uniformity but module lookup is hierarchy-independent.
    """
    table = _parse_design_or_exit(filelist, frontend)
    module = query.find_module(table, name)
    if module is None:
        typer.echo(f"query: module {name!r} not found", err=True)
        raise typer.Exit(code=1)
    _emit_json(asdict(module))


@query_app.command("subtree")
def query_subtree(
    instance_path: Annotated[
        str,
        typer.Argument(
            help="Dot-separated absolute instance path from the top "
            "(e.g. 'counter.u_ff')."
        ),
    ],
    top: _QueryTop,
    filelist: _QueryFilelist,
    frontend: _QueryFrontend = Frontend.verible,
    output_format: Annotated[
        QueryFormat,
        typer.Option(
            "--format",
            case_sensitive=False,
            help="json = nested node objects; tree = the ASCII renderer "
            "rooted at the matched instance.",
        ),
    ] = QueryFormat.json,
) -> None:
    """Print the hierarchy subtree rooted at an instance path.

    Exits 1 when the path doesn't resolve.
    """
    table = _parse_design_or_exit(filelist, frontend)
    root = _build_hierarchy_or_exit(table, top)
    node = _subtree_or_exit(root, instance_path)
    if output_format is QueryFormat.tree:
        tree_render.render(node, sys.stdout)
    else:
        _emit_json(_node_payload(node))


@query_app.command("instances-of")
def query_instances_of(
    module_name: Annotated[
        str, typer.Argument(help="Module name to find instances of.")
    ],
    top: _QueryTop,
    filelist: _QueryFilelist,
    frontend: _QueryFrontend = Frontend.verible,
) -> None:
    """Print every instance of a module across the hierarchy as a JSON list.

    Blackbox instances are included. An empty list is a valid answer
    (exit 0) — the module exists nowhere under ``--top``.
    """
    table = _parse_design_or_exit(filelist, frontend)
    root = _build_hierarchy_or_exit(table, top)
    nodes = query.instances_of(root, module_name)
    _emit_json([_node_payload(n, with_children=False) for n in nodes])


@query_app.command("port-connections")
def query_port_connections(
    instance_path: Annotated[
        str,
        typer.Argument(help="Dot-separated absolute instance path from the top."),
    ],
    top: _QueryTop,
    filelist: _QueryFilelist,
    frontend: _QueryFrontend = Frontend.verible,
) -> None:
    """Print the port-connection list of one instance as JSON.

    The top node has no instantiation site, so querying it yields
    ``[]`` (exit 0). An unresolvable path exits 1.
    """
    table = _parse_design_or_exit(filelist, frontend)
    root = _build_hierarchy_or_exit(table, top)
    _subtree_or_exit(root, instance_path)
    conns = query.port_connections(root, instance_path)
    _emit_json([asdict(c) for c in conns])


@query_app.command("source-snippet")
def query_source_snippet(
    instance_path: Annotated[
        str,
        typer.Argument(help="Dot-separated absolute instance path from the top."),
    ],
    top: _QueryTop,
    filelist: _QueryFilelist,
    frontend: _QueryFrontend = Frontend.verible,
    context: Annotated[
        int,
        typer.Option(
            "--context",
            min=0,
            help="Lines of surrounding context on each side.",
        ),
    ] = 2,
    line_numbers: Annotated[
        bool,
        typer.Option(
            "--line-numbers/--no-line-numbers",
            help="Prefix each line with its 1-indexed source line number "
            "(the LLM-citation format).",
        ),
    ] = True,
) -> None:
    """Print the module-definition source of an instance, with context.

    Plain text on stdout, line-number-prefixed by default — the same
    citation contract as the Python API's ``source_snippet``. Exits 1
    when the path doesn't resolve or no source is available (blackbox,
    or the file vanished between parse and read).
    """
    table = _parse_design_or_exit(filelist, frontend)
    root = _build_hierarchy_or_exit(table, top)
    node = _subtree_or_exit(root, instance_path)
    loc = node.module.location if node.module is not None else None
    snippet = query.source_snippet(
        loc, context_lines=context, with_line_numbers=line_numbers
    )
    if not snippet:
        suffix = " (blackbox — no module definition)" if node.is_blackbox else ""
        typer.echo(
            f"query: no source available for {instance_path!r}{suffix}",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(snippet)


# --- `graph` verb (rtl-buddy-view#126) --------------------------------------
#
# The design tier of the cross-repo knowledge graph. Deliberately a
# sibling of `query` rather than another `--format`: the output is a
# different *artifact* (a durable, mergeable node-link graph keyed by
# design entities), not another rendering of the same view.json
# payload, and it takes no overlay flags — volatile per-run data
# belongs in the results overlay, never in graph.json.


@app.command("graph")
def graph_cmd(
    filelist: Annotated[
        Path,
        typer.Option(
            "--filelist",
            "-f",
            exists=True,
            readable=True,
            help="Path to a filelist (one source file per line).",
        ),
    ],
    top: Annotated[
        str | None,
        typer.Option(
            "--top",
            "-t",
            help="DUT top module. Roots the export unless --tb-top is given.",
        ),
    ] = None,
    tb_top: Annotated[
        str | None,
        typer.Option(
            "--tb-top",
            help="Testbench top module. When given, the export is rooted "
            "here so the SV testbench hierarchy lands in the graph too. "
            "A hint that isn't a real module is auto-corrected from the "
            "elaborated design.",
        ),
    ] = None,
    output_path: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Write graph.json here (parent dirs created). Default: stdout. "
            "A graph-meta.json provenance sidecar is written alongside it.",
        ),
    ] = None,
    frontend: _QueryFrontend = Frontend.verible,
    project_root: Annotated[
        Path | None,
        typer.Option(
            "--project-root",
            exists=True,
            file_okay=False,
            help="Root that node file paths are emitted relative to. "
            "Defaults to the current working directory.",
        ),
    ] = None,
    meta: Annotated[
        bool,
        typer.Option(
            "--meta/--no-meta",
            help="With --output, also write the graph-meta.json sidecar "
            "(input content hashes + generator provenance).",
        ),
    ] = True,
) -> None:
    """Export the elaborated design as a node-link knowledge graph.

    Emits ``graph.json`` v1 (``docs/graph-json-v1.md``): module /
    instance / port / parameter / interface / modport nodes joined by
    ``instantiates``, ``child_of``, ``instance_of``, ``connects``,
    ``implements`` and ``overrides`` edges. The envelope is NetworkX
    node-link JSON, which is what ``graphify merge-graphs`` and
    ``graphify query`` accept.

    Exit codes match the render surface: 1 for a bad filelist / parse
    failure / unresolved top, 2 for a missing top or an unimplemented
    frontend.
    """
    if top is None and tb_top is None:
        typer.echo("error: --top or --tb-top is required", err=True)
        raise typer.Exit(code=2)

    table = _parse_design_or_exit(filelist, frontend)
    tb_top = _resolve_tb_top(table, top, tb_top)
    rendered_top = tb_top if tb_top is not None else top
    assert rendered_top is not None  # guarded above
    root = _build_hierarchy_or_exit(table, rendered_top)

    root_dir = (project_root or Path.cwd()).resolve()
    payload = graph_export.build_graph(
        root,
        table,
        project_root=root_dir,
        project_root_rel=_project_root_rel(root_dir, output_path),
        dut_top=top,
        tb_top=tb_top,
        frontend=frontend.value,
    )

    if output_path is None:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as sink:
        json.dump(payload, sink, indent=2)
        sink.write("\n")
    if meta:
        meta_path = output_path.with_name(f"{output_path.stem}-meta.json")
        try:
            files = parse_filelist(filelist)
        except FilelistError:  # pragma: no cover - parse already succeeded
            files = []
        meta_payload = graph_export.build_meta(payload, files, project_root=root_dir)
        with meta_path.open("w") as sink:
            json.dump(meta_payload, sink, indent=2)
            sink.write("\n")


def _project_root_rel(project_root: Path, output_path: Path | None) -> str:
    """``graph.project_root_rel``: project root relative to graph.json.

    Node ``file`` fields are project-relative, so a consumer holding
    only the graph file needs one hop to get back to the sources:
    ``dirname(graph.json)/project_root_rel/<node file>``. Writing to
    the contract's ``<root>/artefacts/graph/graph.json`` therefore
    yields ``"../.."``. Streaming to stdout has no anchor to be
    relative to, so it degrades to ``"."`` (i.e. "resolve against
    wherever you save this").
    """
    if output_path is None:
        return "."
    out_dir = output_path.resolve().parent
    try:
        return Path(os.path.relpath(project_root, out_dir)).as_posix()
    except ValueError:  # pragma: no cover - different drives on Windows
        return project_root.as_posix()


# Quiet unused-import warning for the type-only re-export the docstring references.
_ = OverlayRegistry
