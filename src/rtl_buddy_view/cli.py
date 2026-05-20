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
"""

from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path
from typing import IO

import typer

from rtl_buddy_view._filelist import FilelistError, parse_filelist
from rtl_buddy_view.annotations import (
    AnnotationsError,
    DomainMap,
)
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.frontend.verible import VeribleParseError, VeribleUnavailable
from rtl_buddy_view.graph import HierarchyError, build_hierarchy
from rtl_buddy_view.overlays import OverlayError, OverlayRegistry, default_registry
from rtl_buddy_view.render import dot as dot_render
from rtl_buddy_view.render import json_render
from rtl_buddy_view.render import mermaid as mermaid_render
from rtl_buddy_view.render import tree as tree_render
from rtl_buddy_view.reset_annotations import (
    ResetAnnotationsError,
    ResetDomainMap,
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


@app.callback(invoke_without_command=True)
def main(
    top: str = typer.Option(
        None,
        "--top",
        "-t",
        help="Top module name.",
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
        "machine-readable (rtl_buddy consumes this).",
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
    clock_legend: bool = typer.Option(
        False,
        "--clock-legend",
        help="Dot format only: emit a side legend listing each clock "
        "with its assigned color swatch. No effect without a clock overlay.",
    ),
) -> None:
    """Render the hierarchy of ``--top`` to ``--format``."""
    registry = default_registry()

    if list_overlays:
        for overlay in registry:
            source = registry.source_of(overlay.name)
            typer.echo(f"{overlay.name}\t{overlay.schema_version}\t({source})")
        raise typer.Exit(code=0)

    # `--top` / `--filelist` are mandatory for actual rendering, but
    # `--list-overlays` is a pure diagnostic so we accept the
    # invocation without them. Validate here instead of marking the
    # options as required at the typer level.
    if top is None or filelist is None:
        typer.echo("error: --top and --filelist are required", err=True)
        raise typer.Exit(code=2)

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
        except (AnnotationsError, ResetAnnotationsError) as e:
            # Loader exceptions carry the overlay's own prefix in
            # their message; we just qualify with the overlay name
            # here so the user sees which artefact failed when
            # multiple --overlay flags were supplied.
            typer.echo(f"overlay {name}: {e}", err=True)
            raise typer.Exit(code=1) from None

    try:
        files = parse_filelist(filelist)
    except FilelistError as e:
        typer.echo(f"filelist: {e}", err=True)
        raise typer.Exit(code=1) from None

    try:
        table = parse_to_modules(files, frontend=frontend)
    except (VeribleUnavailable, VeribleParseError) as e:
        typer.echo(f"verible: {e}", err=True)
        raise typer.Exit(code=1) from None
    except NotImplementedError as e:
        typer.echo(f"frontend: {e}", err=True)
        raise typer.Exit(code=2) from None

    try:
        root = build_hierarchy(table, top)
    except HierarchyError as e:
        typer.echo(f"hierarchy: {e}", err=True)
        raise typer.Exit(code=1) from None

    domain_map: DomainMap | None = annotations.get("clock")  # type: ignore[assignment]
    reset_map: ResetDomainMap | None = annotations.get("reset")  # type: ignore[assignment]

    sink: IO[str]
    if output_path is None:
        sink = sys.stdout
        _render(root, output_format, sink, domain_map, reset_map, clock_legend)
    else:
        with output_path.open("w") as sink:
            _render(root, output_format, sink, domain_map, reset_map, clock_legend)


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
        if not path.exists():
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
    clock_legend: bool,
) -> None:
    if fmt is OutputFormat.tree:
        tree_render.render(root, sink, domain_map=domain_map, reset_map=reset_map)
    elif fmt is OutputFormat.dot:
        dot_render.render(
            root,
            sink,
            domain_map=domain_map,
            reset_map=reset_map,
            with_legend=clock_legend,
        )
    elif fmt is OutputFormat.mermaid:
        mermaid_render.render(root, sink, domain_map=domain_map, reset_map=reset_map)
    elif fmt is OutputFormat.json:
        json_render.render(root, sink, domain_map=domain_map, reset_map=reset_map)
    else:  # pragma: no cover
        raise ValueError(f"Unknown format: {fmt}")


# Quiet unused-import warning for the type-only re-export the docstring references.
_ = OverlayRegistry
