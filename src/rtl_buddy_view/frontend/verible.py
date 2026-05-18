"""Verible frontend — subprocess wrapper around ``verible-verilog-syntax``.

Pipeline per file:

1. Locate the Verible binary (PATH wins; otherwise the vendored copy
   under ``vendor/verible/``). Resolved by :func:`locate_binary`.
2. Run ``verible-verilog-syntax --export_json --printtree <file>``
   to produce the JSON CST.
3. Walk the CST to extract module declarations. Phase 1 captures the
   module name and source location; ports / parameters / instances
   are stubbed (filled in by follow-up PRs as the fixture suite
   grows).
4. Return a :class:`ModuleTable` keyed by module name.

The cache layer (CST keyed on file content hash) and the broader
extractor coverage are tracked separately in
[#1](https://github.com/rtl-buddy/rtl-buddy-view/issues/1).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from rtl_buddy_view._offsets import OffsetIndex
from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.extractor import (
    Instance,
    Module,
    ModuleTable,
    Port,
    SourceLocation,
)


class VeribleUnavailable(RuntimeError):
    """Raised when the verible-verilog-syntax binary can't be found."""


class VeribleParseError(RuntimeError):
    """Raised when Verible could not produce a CST for a file."""


def locate_binary(name: str = "verible-verilog-syntax") -> Path:
    """Return the path to a verible-* binary or raise :class:`VeribleUnavailable`.

    Resolution order: ``PATH`` first (so Homebrew-installed Verible
    or whatever's already on the system wins), then the vendored copy
    under ``vendor/verible/``. Run ``scripts/fetch_verible.py`` to
    install the pinned release.
    """
    found = find_binary(name)
    if found is None:
        raise VeribleUnavailable(
            f"{name} not found on PATH or in vendor/verible/. "
            "Install it with `uv run python scripts/fetch_verible.py` "
            "or via Homebrew (`brew install verible`)."
        )
    return found


def parse(files: list[Path]) -> ModuleTable:
    """Parse the given SV files into a :class:`ModuleTable`.

    Each module declaration found anywhere in ``files`` becomes an
    entry in :attr:`ModuleTable.modules_by_name`. Module names that
    collide across files raise :class:`VeribleParseError` — Phase 1
    treats the design as a flat namespace; library / package
    scoping is a follow-up.
    """
    binary = locate_binary()
    table = ModuleTable()
    for path in files:
        text = path.read_text()
        source_bytes = text.encode("utf-8")
        offsets = OffsetIndex.build(text)
        cst = _run_verible(binary, path)
        for mod in _walk_modules(
            cst, file=str(path), offsets=offsets, source=source_bytes
        ):
            if mod.name in table.modules_by_name:
                raise VeribleParseError(
                    f"Duplicate module definition {mod.name!r}: already "
                    f"defined at {table.modules_by_name[mod.name].location}, "
                    f"redefined at {mod.location}."
                )
            table.modules_by_name[mod.name] = mod
    return table


# --- internals ---------------------------------------------------------------


def _run_verible(binary: Path, path: Path) -> dict:
    proc = subprocess.run(
        [str(binary), "--export_json", "--printtree", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise VeribleParseError(
            f"verible-verilog-syntax exited {proc.returncode} for {path}: "
            f"{proc.stderr.strip()}"
        )
    payload = json.loads(proc.stdout)
    file_entry = payload.get(str(path))
    if file_entry is None:
        raise VeribleParseError(
            f"verible-verilog-syntax produced no tree for {path}; "
            f"likely a syntax error (stderr: {proc.stderr.strip()})."
        )
    tree = file_entry.get("tree")
    if tree is None:
        raise VeribleParseError(f"verible-verilog-syntax produced no tree for {path}.")
    return tree


def _walk_modules(
    tree: dict,
    *,
    file: str,
    offsets: OffsetIndex,
    source: bytes,
) -> list[Module]:
    """Yield one :class:`Module` per ``kModuleDeclaration`` in the CST."""
    out: list[Module] = []
    for node in _iter_nodes_with_tag(tree, "kModuleDeclaration"):
        header = _first_child_with_tag(node, "kModuleHeader")
        if header is None:
            continue
        name_node = _first_child_with_tag(header, "SymbolIdentifier")
        if name_node is None or "text" not in name_node:
            continue
        name = name_node["text"]
        span = _node_span(node)
        loc = (
            SourceLocation(file=file)
            if span is None
            else _location(file, offsets, span[0], span[1])
        )
        ports = _extract_ports(header, file=file, offsets=offsets, source=source)
        instances = _extract_instances(node, file=file, offsets=offsets)
        out.append(
            Module(
                name=name,
                ports=ports,
                parameters=(),
                instances=instances,
                location=loc,
            )
        )
    return out


# --- port / instance walkers -------------------------------------------------


_DIRECTION_TAGS: dict[str, str] = {
    "input": "input",
    "output": "output",
    "inout": "inout",
}


def _extract_ports(
    header: dict, *, file: str, offsets: OffsetIndex, source: bytes
) -> tuple[Port, ...]:
    """Extract ANSI-style ports from a ``kModuleHeader``.

    Verible has two ``kParenGroup`` children under ``kModuleHeader``
    when the module has parameters: the first for ``#(...)``, the
    second for the port list. The port list always appears alongside
    a ``kPortDeclarationList`` so we filter by that child tag rather
    than positional indexing — robust to whether parameters are
    declared or not.
    """
    port_list_paren = None
    for child in header.get("children", ()) or ():
        if not isinstance(child, dict) or child.get("tag") != "kParenGroup":
            continue
        if _first_child_with_tag(child, "kPortDeclarationList") is not None:
            port_list_paren = child
            break
    if port_list_paren is None:
        return ()
    decl_list = _first_child_with_tag(port_list_paren, "kPortDeclarationList")
    if decl_list is None:
        return ()
    out: list[Port] = []
    for decl in decl_list.get("children", ()) or ():
        if not isinstance(decl, dict) or decl.get("tag") != "kPortDeclaration":
            continue
        out.append(_port_from_decl(decl, file=file, offsets=offsets, source=source))
    return tuple(out)


def _port_from_decl(
    decl: dict, *, file: str, offsets: OffsetIndex, source: bytes
) -> Port:
    direction: str | None = None
    type_text: str | None = None
    name = "<unknown>"
    name_node: dict | None = None
    for child in decl.get("children", ()) or ():
        if not isinstance(child, dict):
            continue
        tag = child.get("tag")
        if direction is None and tag in _DIRECTION_TAGS:
            direction = _DIRECTION_TAGS[tag]
        elif tag == "kDataType":
            type_text = _source_slice(child, source)
        elif tag == "kUnqualifiedId":
            sym = _first_child_with_tag(child, "SymbolIdentifier")
            if sym is not None and "text" in sym:
                name = sym["text"]
                name_node = sym
    span = _node_span(name_node) if name_node is not None else _node_span(decl)
    loc = (
        SourceLocation(file=file)
        if span is None
        else _location(file, offsets, span[0], span[1])
    )
    return Port(name=name, direction=direction, type_text=type_text, location=loc)  # type: ignore[arg-type]


def _extract_instances(
    module_node: dict, *, file: str, offsets: OffsetIndex
) -> tuple[Instance, ...]:
    """Extract child instances from a ``kModuleDeclaration``.

    Each instantiation lives under
    ``kModuleItemList > kDataDeclaration > kInstantiationBase``. A
    single ``kInstantiationBase`` can declare multiple gate instances
    sharing the same module type — we emit one :class:`Instance` per
    ``kGateInstance``.
    """
    item_list = _first_child_with_tag(module_node, "kModuleItemList")
    if item_list is None:
        return ()
    out: list[Instance] = []
    for data_decl in item_list.get("children", ()) or ():
        if (
            not isinstance(data_decl, dict)
            or data_decl.get("tag") != "kDataDeclaration"
        ):
            continue
        base = _first_child_with_tag(data_decl, "kInstantiationBase")
        if base is None:
            continue
        inst_type = _first_child_with_tag(base, "kInstantiationType")
        gate_list = _first_child_with_tag(base, "kGateInstanceRegisterVariableList")
        if inst_type is None or gate_list is None:
            continue
        module_name_node = _find_first_sym(inst_type)
        if module_name_node is None:
            continue
        module_name = module_name_node.get("text", "")
        if not module_name:
            continue
        for gate in gate_list.get("children", ()) or ():
            if not isinstance(gate, dict) or gate.get("tag") != "kGateInstance":
                continue
            inst_name_node = _first_child_with_tag(gate, "SymbolIdentifier")
            if inst_name_node is None or "text" not in inst_name_node:
                continue
            inst_name = inst_name_node["text"]
            span = _node_span(gate)
            loc = (
                SourceLocation(file=file)
                if span is None
                else _location(file, offsets, span[0], span[1])
            )
            out.append(
                Instance(
                    name=inst_name,
                    module_name=module_name,
                    param_overrides=(),
                    port_connections=(),
                    location=loc,
                )
            )
    return tuple(out)


def _find_first_sym(node: dict) -> dict | None:
    """First ``SymbolIdentifier`` reachable in a depth-first walk."""
    if not isinstance(node, dict):
        return None
    if node.get("tag") == "SymbolIdentifier" and "text" in node:
        return node
    for child in node.get("children", ()) or ():
        if isinstance(child, dict):
            hit = _find_first_sym(child)
            if hit is not None:
                return hit
    return None


def _source_slice(node: dict, source: bytes) -> str | None:
    """Return the verbatim source text covered by ``node``.

    Verible omits the ``text`` field on keyword leaves (the ``logic``
    keyword has ``tag: "logic"`` and byte offsets but no ``text``),
    so reconstructing the source from token texts loses content.
    Using the byte span against the original file preserves comments
    and whitespace exactly — useful for diagram labels and as LLM
    context when the snippet is fed back.
    """
    span = _node_span(node)
    if span is None:
        return None
    start, end = span
    return source[start:end].decode("utf-8", errors="replace")


def _iter_nodes_with_tag(node: dict | None, tag: str):
    """Depth-first iterator over CST nodes with the given ``tag``.

    Yields the node dicts themselves so callers can read further
    children. Skips ``None`` entries that Verible emits as
    placeholders for absent optional grammar slots.
    """
    if node is None:
        return
    if isinstance(node, dict):
        if node.get("tag") == tag:
            yield node
        for child in node.get("children", ()) or ():
            yield from _iter_nodes_with_tag(child, tag)


def _first_child_with_tag(node: dict, tag: str) -> dict | None:
    for child in node.get("children", ()) or ():
        if isinstance(child, dict) and child.get("tag") == tag:
            return child
    return None


def _node_span(node: dict) -> tuple[int, int] | None:
    """Return the byte ``(start, end)`` of all leaf descendants of ``node``.

    Verible only puts ``start``/``end`` on leaf (token) nodes;
    interior tags like ``kModuleDeclaration`` carry no offsets, so we
    derive the span by walking the leaves. Returns ``None`` if the
    subtree contains no positioned leaf at all.
    """
    starts: list[int] = []
    ends: list[int] = []

    def visit(n: object) -> None:
        if isinstance(n, dict):
            if "start" in n:
                starts.append(int(n["start"]))
            if "end" in n:
                ends.append(int(n["end"]))
            for c in n.get("children", ()) or ():
                visit(c)

    visit(node)
    if not starts:
        return None
    return (min(starts), max(ends) if ends else min(starts))


def _location(
    file: str, offsets: OffsetIndex, start: int, end: int | None
) -> SourceLocation:
    sl, sc = offsets.line_col(start)
    el: int | None = None
    ec: int | None = None
    if end is not None:
        el, ec = offsets.line_col(end)
    return SourceLocation(
        file=file,
        start_line=sl,
        start_column=sc,
        end_line=el,
        end_column=ec,
    )
