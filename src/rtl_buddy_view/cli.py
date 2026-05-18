"""Typer CLI for ``rtl-buddy-view``.

Phase 1 surface::

    rtl-buddy-view --top <module> --filelist <file> \\
                   [--format tree|dot] [--output <path>] \\
                   [--frontend verible|slang]

The CLI is wired through the analyzer pipeline (frontend → extractor →
graph → renderer) but the analyzer layers themselves are
``NotImplementedError`` stubs until the Phase 1 PRs land — see
[rtl-buddy/rtl-buddy-view#1](https://github.com/rtl-buddy/rtl-buddy-view/issues/1).
Running ``--help`` works today.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import typer

from rtl_buddy_view.frontend import Frontend

app = typer.Typer(
    help="RTL hierarchy and connectivity visualization.",
    no_args_is_help=True,
)


class OutputFormat(str, Enum):
    tree = "tree"
    dot = "dot"


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
        help="Output format. tree = ASCII; dot = Graphviz.",
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
) -> None:
    """Render the hierarchy of ``--top`` to ``--format``."""
    typer.echo(
        f"rtl-buddy-view: pre-alpha. top={top} filelist={filelist} "
        f"format={output_format.value} frontend={frontend.value} "
        f"output={output_path or '<stdout>'}",
        err=True,
    )
    typer.echo(
        "Analyzer pipeline is not yet implemented — see "
        "https://github.com/rtl-buddy/rtl-buddy-view/issues/1",
        err=True,
    )
    raise typer.Exit(code=2)
