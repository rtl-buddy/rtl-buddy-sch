"""Typer CLI for ``rtl-buddy-view``.

Phase 1 surface::

    rtl-buddy-view --top <module> --filelist <file> \\
                   [--format tree|dot] [--output <path>] \\
                   [--frontend verible|slang]

Routes through frontend → extractor → graph → renderer. The Verible
frontend is wired and supports the empty-module case end-to-end
today; broader extractor coverage (ports, parameters, instances)
lands in follow-up PRs against issue
[#1](https://github.com/rtl-buddy/rtl-buddy-view/issues/1).
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
    load_domain_map,
)
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.frontend.verible import VeribleParseError, VeribleUnavailable
from rtl_buddy_view.graph import HierarchyError, build_hierarchy
from rtl_buddy_view.render import dot as dot_render
from rtl_buddy_view.render import json_render
from rtl_buddy_view.render import mermaid as mermaid_render
from rtl_buddy_view.render import tree as tree_render
from rtl_buddy_view.reset_annotations import (
    ResetAnnotationsError,
    ResetDomainMap,
    load_reset_domain_map,
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
        ...,
        "--top",
        "-t",
        help="Top module name.",
    ),
    filelist: Path = typer.Option(
        ...,
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
    cdc_annotations: Path | None = typer.Option(
        None,
        "--cdc-annotations",
        exists=True,
        readable=True,
        help="Optional clock-domain map JSON emitted by "
        "`rtl-buddy-cdc --emit-domain-map` (rtl-buddy-cdc#106). "
        "When present, renderers overlay clock-domain context onto "
        "the hierarchy; absent → identical to un-annotated output.",
    ),
    rdc_annotations: Path | None = typer.Option(
        None,
        "--rdc-annotations",
        exists=True,
        readable=True,
        help="Optional reset-domain map JSON emitted by "
        "`rtl-buddy-cdc --emit-reset-domain-map` (rtl-buddy-cdc#108). "
        "When present, renderers overlay per-flop reset-source and "
        "RDC crossings onto the hierarchy; absent → no reset overlay. "
        "Composable with --cdc-annotations.",
    ),
    clock_legend: bool = typer.Option(
        False,
        "--clock-legend",
        help="Dot format only: emit a side legend listing each clock "
        "with its assigned color swatch. No effect without "
        "--cdc-annotations.",
    ),
) -> None:
    """Render the hierarchy of ``--top`` to ``--format``."""
    domain_map: DomainMap | None = None
    if cdc_annotations is not None:
        try:
            domain_map = load_domain_map(cdc_annotations)
        except AnnotationsError as e:
            typer.echo(f"cdc-annotations: {e}", err=True)
            raise typer.Exit(code=1) from None

    reset_map: ResetDomainMap | None = None
    if rdc_annotations is not None:
        try:
            reset_map = load_reset_domain_map(rdc_annotations)
        except ResetAnnotationsError as e:
            typer.echo(f"rdc-annotations: {e}", err=True)
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

    sink: IO[str]
    if output_path is None:
        sink = sys.stdout
        _render(root, output_format, sink, domain_map, reset_map, clock_legend)
    else:
        with output_path.open("w") as sink:
            _render(root, output_format, sink, domain_map, reset_map, clock_legend)


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
