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

import dataclasses
import json
import re
import subprocess
from pathlib import Path

from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.cst_cache import cache_root, get_or_compute
from rtl_buddy_view.offsets import OffsetIndex
from rtl_buddy_view.extractor import (
    Assign,
    DpiFunction,
    GenerateArm,
    GenerateConditional,
    GenerateItem,
    GenerateLoop,
    Instance,
    Interface,
    InterfaceSignal,
    Module,
    Modport,
    ModuleTable,
    NetDecl,
    Parameter,
    ParameterOverride,
    Port,
    PortConnection,
    SourceLocation,
)
from rtl_buddy_view.generate import elaborate, module_env


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
        cst = get_or_compute(
            path,
            verible_binary=binary,
            compute=_run_verible,
        )
        modules = _walk_modules(
            cst, file=str(path), offsets=offsets, source=source_bytes
        )
        if not modules:
            _check_no_module_declared(path, text)
        for mod in modules:
            if mod.name in table.modules_by_name:
                raise VeribleParseError(
                    f"Duplicate module definition {mod.name!r}: already "
                    f"defined at {table.modules_by_name[mod.name].location}, "
                    f"redefined at {mod.location}."
                )
            table.modules_by_name[mod.name] = mod
        for iface in _walk_interfaces(
            cst, file=str(path), offsets=offsets, source=source_bytes
        ):
            # Duplicate interface definitions are silently last-wins:
            # rtl-buddy-view's flat-namespace assumption applies here
            # too, but interfaces appear less often than modules and
            # producers sometimes vendor the same interface header
            # into multiple filelists. Raising would be louder than
            # the benefit; consumers care about *some* definition
            # being available for flattening, not strict identity.
            table.interfaces_by_name[iface.name] = iface
    return table


# --- internals ---------------------------------------------------------------


def _check_no_module_declared(path: Path, text: str) -> None:
    """Raise if the source plainly declares a module the CST walk missed.

    A CST walk that yields zero modules is normal for a package /
    interface / macro-only file, so it can't be an error on its own.
    It is wrong when the source carries an unconditional ``module``
    declaration — that means the CST isn't the tree we expect (a
    wrong-shape cache entry, rtl-buddy-sch#186) and the design would
    otherwise fail much later with a misleading ``Known modules: []``.
    """
    found = _find_module_declaration(text)
    if found is None:
        return
    line_no, name = found
    raise VeribleParseError(
        f"Verible CST for {path} yields no module declarations, but the "
        f"source declares module {name!r} at line {line_no}. The CST is "
        "probably a wrong-shape or stale cache entry: re-run with "
        "RTL_BUDDY_NO_CACHE=1, or delete the cache under "
        f"{cache_root()}."
    )


# Strings first so a ``//`` inside a string isn't read as a comment,
# and comments next so a quote inside a comment isn't read as a string.
_STRIP_RE = re.compile(r'"(?:\\.|[^"\\\n])*"|/\*.*?\*/|//[^\n]*', re.DOTALL)
# ``module <ident>`` opening a line, after optional ``(* attr *)``s.
# Deliberately narrow: ``macromodule`` (a different CST tag),
# ``extern module`` (a prototype, not a declaration) and macro-named
# modules (``module `NAME``) don't match, so they can never trip the
# check.
_MODULE_DECL_RE = re.compile(
    r"^\s*(?:\(\*.*?\*\)\s*)*module\s+(?:(?:static|automatic)\s+)?"
    r"([A-Za-z_][A-Za-z0-9_$]*)"
)
_COND_OPEN_RE = re.compile(r"^\s*`(?:ifdef|ifndef)\b")
_COND_CLOSE_RE = re.compile(r"^\s*`endif\b")
_DEFINE_RE = re.compile(r"^\s*`define\b")


def _find_module_declaration(text: str) -> tuple[int, str] | None:
    """Return ``(line, name)`` of the first unconditional module declaration.

    Conservative by design — it only has to be right when it answers,
    so anything it can't judge cheaply is skipped:

    - comments and string literals are blanked first (newlines kept,
      so line numbers stay true);
    - lines inside any `` `ifdef`` / `` `ifndef`` block are skipped,
      since which branch Verible parses depends on its macro defaults;
    - `` `define`` bodies (including ``\\`` continuation lines) are
      skipped, since a macro that expands to a module isn't one until
      it is used.
    """

    def _blank(m: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))

    stripped = _STRIP_RE.sub(_blank, text)
    depth = 0
    in_define = False
    for line_no, line in enumerate(stripped.splitlines(), start=1):
        if in_define:
            in_define = line.rstrip().endswith("\\")
            continue
        if _DEFINE_RE.match(line):
            in_define = line.rstrip().endswith("\\")
            continue
        if _COND_OPEN_RE.match(line):
            depth += 1
            continue
        if _COND_CLOSE_RE.match(line):
            depth = max(depth - 1, 0)
            continue
        if depth:
            continue
        m = _MODULE_DECL_RE.match(line)
        if m:
            return line_no, m.group(1)
    return None


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
        parameters = _extract_parameters(
            header, file=file, offsets=offsets, source=source
        )
        instances = _extract_instances(node, file=file, offsets=offsets, source=source)
        dpi_functions = _extract_dpi_functions(
            node, file=file, offsets=offsets, source=source
        )
        assigns = _extract_assigns(node, file=file, offsets=offsets, source=source)
        procedural_assigns, procedural_conditions = _extract_procedural(
            node, file=file, offsets=offsets, source=source
        )
        item_list = _first_child_with_tag(node, "kModuleItemList")
        local_params = _scope_params(
            item_list, file=file, offsets=offsets, source=source
        )
        generate_body: tuple[GenerateItem, ...] = ()
        if item_list is not None and _has_generate(item_list):
            # rtl-buddy-sch#209: keep the body as a template and unroll
            # it against the module's own defaults. The hierarchy
            # builder re-unrolls it per placement when overrides move
            # a bound.
            generate_body = _generate_scope(
                item_list, file=file, offsets=offsets, source=source
            )
        net_decls = _extract_net_decls(
            node,
            file=file,
            offsets=offsets,
            source=source,
            port_names={p.name for p in ports},
        )
        module = Module(
            name=name,
            ports=ports,
            parameters=parameters,
            instances=instances,
            location=loc,
            dpi_functions=dpi_functions,
            assigns=assigns,
            net_decls=net_decls,
            generate_body=generate_body,
            local_params=local_params,
            procedural_assigns=procedural_assigns,
            procedural_conditions=procedural_conditions,
        )
        if generate_body:
            instances, assigns = elaborate(generate_body, module_env(module))
            module = dataclasses.replace(module, instances=instances, assigns=assigns)
        out.append(module)
    return out


# --- interface walkers ------------------------------------------------------


def _walk_interfaces(
    tree: dict,
    *,
    file: str,
    offsets: OffsetIndex,
    source: bytes,
) -> list[Interface]:
    """Yield one :class:`Interface` per ``kInterfaceDeclaration`` in the CST.

    Shape (confirmed against the sandbox + fixture)::

        kInterfaceDeclaration
          kModuleHeader
            interface
            SymbolIdentifier               <- name
            kFormalParameterListDeclaration (optional)
          kModuleItemList
            kDataDeclaration               <- signals
              kInstantiationBase
                kInstantiationType > kDataType
                kGateInstanceRegisterVariableList
                  kRegisterVariable
                    SymbolIdentifier        <- signal name
            kModportDeclaration            <- modports
              kModportItemList
                kModportItem
                  SymbolIdentifier         <- modport name
                  kParenGroup
                    kModportPortList ...

    Modports inside generate blocks or nested scopes are out of
    scope for #105 — flat interfaces only.
    """
    out: list[Interface] = []
    for node in _iter_nodes_with_tag(tree, "kInterfaceDeclaration"):
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
        parameters = _extract_parameters(
            header, file=file, offsets=offsets, source=source
        )
        item_list = _first_child_with_tag(node, "kModuleItemList")
        signals = _extract_interface_signals(
            item_list, file=file, offsets=offsets, source=source
        )
        modports = _extract_modports(item_list, file=file, offsets=offsets)
        out.append(
            Interface(
                name=name,
                parameters=parameters,
                signals=signals,
                modports=modports,
                location=loc,
            )
        )
    return out


def _extract_interface_signals(
    item_list: dict | None,
    *,
    file: str,
    offsets: OffsetIndex,
    source: bytes,
) -> tuple[InterfaceSignal, ...]:
    """Pull ``kDataDeclaration`` rows under ``kModuleItemList``.

    Each kDataDeclaration carries one or more kRegisterVariable
    entries (Verible's name for variable-style net declarations,
    used for ``logic`` / ``wire`` signals). We capture the first
    name + the data-type slice — enough for the flatten step to
    synthesize a :class:`Port` per signal. (#105.)
    """
    if item_list is None:
        return ()
    out: list[InterfaceSignal] = []
    for child in item_list.get("children", ()) or ():
        if not isinstance(child, dict) or child.get("tag") != "kDataDeclaration":
            continue
        base = _first_child_with_tag(child, "kInstantiationBase")
        if base is None:
            continue
        inst_type = _first_child_with_tag(base, "kInstantiationType")
        type_text = None
        if inst_type is not None:
            dtype = _first_child_with_tag(inst_type, "kDataType")
            if dtype is not None:
                type_text = _source_slice(dtype, source)
        var_list = _first_child_with_tag(base, "kGateInstanceRegisterVariableList")
        if var_list is None:
            continue
        for var in var_list.get("children", ()) or ():
            if not isinstance(var, dict) or var.get("tag") != "kRegisterVariable":
                continue
            sym = _first_child_with_tag(var, "SymbolIdentifier")
            if sym is None or "text" not in sym:
                continue
            span = _node_span(sym)
            loc = (
                SourceLocation(file=file)
                if span is None
                else _location(file, offsets, span[0], span[1])
            )
            out.append(
                InterfaceSignal(
                    name=sym["text"],
                    type_text=type_text,
                    location=loc,
                )
            )
    return tuple(out)


def _extract_modports(
    item_list: dict | None,
    *,
    file: str,
    offsets: OffsetIndex,
) -> tuple[Modport, ...]:
    """Pull ``kModportDeclaration`` rows from the interface body.

    A single ``modport`` keyword can declare one or more modports
    (comma-separated). For each ``kModportItem`` we capture the
    name + per-direction signal lists. Mixed-direction modports
    (``modport sub(input a, output b)``) get one
    ``kModportSimplePortsDeclaration`` per direction; same-direction
    shorthand (``output a, b``) puts both signals under a single
    declaration. We handle both.
    """
    if item_list is None:
        return ()
    out: list[Modport] = []
    for child in item_list.get("children", ()) or ():
        if not isinstance(child, dict) or child.get("tag") != "kModportDeclaration":
            continue
        items = _first_child_with_tag(child, "kModportItemList")
        if items is None:
            continue
        for item in items.get("children", ()) or ():
            if not isinstance(item, dict) or item.get("tag") != "kModportItem":
                continue
            sym = _first_child_with_tag(item, "SymbolIdentifier")
            if sym is None or "text" not in sym:
                continue
            paren = _first_child_with_tag(item, "kParenGroup")
            port_list = (
                _first_child_with_tag(paren, "kModportPortList") if paren else None
            )
            inputs: list[str] = []
            outputs: list[str] = []
            inouts: list[str] = []
            if port_list is not None:
                for decl in port_list.get("children", ()) or ():
                    if (
                        not isinstance(decl, dict)
                        or decl.get("tag") != "kModportSimplePortsDeclaration"
                    ):
                        continue
                    direction: str | None = None
                    bucket: list[str] | None = None
                    for sub in decl.get("children", ()) or ():
                        if not isinstance(sub, dict):
                            continue
                        sub_tag = sub.get("tag")
                        if sub_tag in _DIRECTION_TAGS:
                            direction = _DIRECTION_TAGS[sub_tag]
                            if direction == "input":
                                bucket = inputs
                            elif direction == "output":
                                bucket = outputs
                            else:
                                bucket = inouts
                        elif sub_tag == "kModportSimplePort" and bucket is not None:
                            sig = _first_child_with_tag(sub, "SymbolIdentifier")
                            if sig is not None and "text" in sig:
                                bucket.append(sig["text"])
            span = _node_span(sym)
            loc = (
                SourceLocation(file=file)
                if span is None
                else _location(file, offsets, span[0], span[1])
            )
            out.append(
                Modport(
                    name=sym["text"],
                    inputs=tuple(inputs),
                    outputs=tuple(outputs),
                    inouts=tuple(inouts),
                    location=loc,
                )
            )
    return tuple(out)


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
    port_kind: str = "wire"
    interface_type: str | None = None
    modport: str | None = None
    for child in decl.get("children", ()) or ():
        if not isinstance(child, dict):
            continue
        tag = child.get("tag")
        if direction is None and tag in _DIRECTION_TAGS:
            direction = _DIRECTION_TAGS[tag]
        elif tag == "kDataType":
            type_text = _source_slice(child, source)
            iface_header = _first_child_with_tag(child, "kInterfacePortHeader")
            if iface_header is not None:
                port_kind = "interface"
                interface_type, modport = _interface_port_idents(iface_header)
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
    return Port(
        name=name,
        direction=direction,  # type: ignore[arg-type]
        type_text=type_text,
        location=loc,
        port_kind=port_kind,  # type: ignore[arg-type]
        interface_type=interface_type,
        modport=modport,
    )


def _interface_port_idents(iface_header: dict) -> tuple[str | None, str | None]:
    """Extract ``(interface_type, modport)`` from a ``kInterfacePortHeader``.

    Verible's CST shape (confirmed against
    ``tests/fixtures/interface_port_module/test_module_3.sv``)::

        kInterfacePortHeader
          kUnqualifiedId
            SymbolIdentifier   <- interface type, e.g. "test_mem_if"
          .                    <- optional, only present with a modport
          SymbolIdentifier     <- modport, e.g. "sub"

    The interface type lives one level deep (wrapped in
    ``kUnqualifiedId``); the modport, when present, sits as a direct
    leaf ``SymbolIdentifier`` after the ``.`` separator. ``None`` for
    either field signals "absent" so callers can keep the
    ``interface_type``/``modport`` slots typed.
    """
    interface_type: str | None = None
    modport: str | None = None
    for child in iface_header.get("children", ()) or ():
        if not isinstance(child, dict):
            continue
        tag = child.get("tag")
        if interface_type is None and tag == "kUnqualifiedId":
            sym = _first_child_with_tag(child, "SymbolIdentifier")
            if sym is not None and "text" in sym:
                interface_type = sym["text"]
        elif tag == "SymbolIdentifier" and "text" in child:
            # Direct-leaf SymbolIdentifier — the modport leg.
            modport = child["text"]
    return interface_type, modport


def _extract_parameters(
    header: dict, *, file: str, offsets: OffsetIndex, source: bytes
) -> tuple[Parameter, ...]:
    """Extract module-level parameter declarations from ``kModuleHeader``.

    Layout::

        kModuleHeader
          kFormalParameterListDeclaration
            #
            kParenGroup
              kFormalParameterList
                kParamDeclaration
                  parameter
                  kParamType
                    kTypeInfo …
                    SymbolIdentifier  <-- the name
                  kTrailingAssign
                    =
                    kExpression       <-- the default value (optional)

    Captures the parameter name and (when present) the verbatim
    default-value source slice. Type expressions are not parsed in
    Phase 1.
    """
    formal = _first_child_with_tag(header, "kFormalParameterListDeclaration")
    if formal is None:
        return ()
    paren = _first_child_with_tag(formal, "kParenGroup")
    if paren is None:
        return ()
    plist = _first_child_with_tag(paren, "kFormalParameterList")
    if plist is None:
        return ()
    out: list[Parameter] = []
    for decl in plist.get("children", ()) or ():
        if not isinstance(decl, dict) or decl.get("tag") != "kParamDeclaration":
            continue
        out.append(_param_from_decl(decl, file=file, offsets=offsets, source=source))
    return tuple(out)


def _param_from_decl(
    decl: dict, *, file: str, offsets: OffsetIndex, source: bytes
) -> Parameter:
    name = "<unknown>"
    name_node: dict | None = None
    default_text: str | None = None
    ptype = _first_child_with_tag(decl, "kParamType")
    if ptype is not None:
        for child in ptype.get("children", ()) or ():
            if not isinstance(child, dict):
                continue
            # A typed parameter (``parameter int W``) carries its name
            # as a direct SymbolIdentifier; an untyped one
            # (``parameter W``, and every later declarator of a
            # ``#(parameter A = 1, B = 2)`` list) wraps it in a
            # kUnqualifiedId.
            sym = (
                child
                if child.get("tag") == "SymbolIdentifier"
                else _first_child_with_tag(child, "SymbolIdentifier")
                if child.get("tag") == "kUnqualifiedId"
                else None
            )
            if sym is not None:
                if "text" in sym:
                    name = sym["text"]
                    name_node = sym
                break
    trailing = _first_child_with_tag(decl, "kTrailingAssign")
    if trailing is not None:
        expr = _first_child_with_tag(trailing, "kExpression")
        if expr is not None:
            default_text = _source_slice(expr, source)
    span = _node_span(name_node) if name_node is not None else _node_span(decl)
    loc = (
        SourceLocation(file=file)
        if span is None
        else _location(file, offsets, span[0], span[1])
    )
    return Parameter(name=name, default_text=default_text, location=loc)


def _extract_instances(
    module_node: dict, *, file: str, offsets: OffsetIndex, source: bytes
) -> tuple[Instance, ...]:
    """Extract child instances from a ``kModuleDeclaration``.

    Each instantiation lives under
    ``kModuleItemList > kDataDeclaration > kInstantiationBase``. A
    single ``kInstantiationBase`` can declare multiple gate instances
    sharing the same module type — we emit one :class:`Instance` per
    ``kGateInstance``. Named parameter overrides live alongside the
    type's SymbolIdentifier under ``kUnqualifiedId``; we extract them
    via :func:`_extract_param_overrides`.
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
        out.extend(
            _instances_from_decl(data_decl, file=file, offsets=offsets, source=source)
        )
    return tuple(out)


def _instances_from_decl(
    data_decl: dict, *, file: str, offsets: OffsetIndex, source: bytes
) -> list[Instance]:
    """The instances one ``kDataDeclaration`` declares (none for a net)."""
    base = _first_child_with_tag(data_decl, "kInstantiationBase")
    if base is None:
        return []
    inst_type = _first_child_with_tag(base, "kInstantiationType")
    gate_list = _first_child_with_tag(base, "kGateInstanceRegisterVariableList")
    if inst_type is None or gate_list is None:
        return []
    module_name_node = _find_first_sym(inst_type)
    if module_name_node is None:
        return []
    module_name = module_name_node.get("text", "")
    if not module_name:
        return []
    param_overrides = _extract_param_overrides(
        inst_type, file=file, offsets=offsets, source=source
    )
    out: list[Instance] = []
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
        port_connections = _extract_port_connections(
            gate, file=file, offsets=offsets, source=source
        )
        out.append(
            Instance(
                name=inst_name,
                module_name=module_name,
                param_overrides=param_overrides,
                port_connections=port_connections,
                location=loc,
            )
        )
    return out


# --- generate templates (rtl-buddy-sch#209) --------------------------------

#: The three generate constructs. Each one counts toward its scope's
#: ``genblk<N>`` numbering, named or not (IEEE 1800-2017 § 27.6).
_GENERATE_TAGS = (
    "kLoopGenerateConstruct",
    "kConditionalGenerateConstruct",
    "kCaseGenerateConstruct",
)

_GENBLK_RE = re.compile(r"^genblk\d+$")


def _has_generate(item_list: dict) -> bool:
    """Whether a module body holds any generate construct or region."""
    return any(
        True
        for _ in _iter_nodes_with_tags(item_list, (*_GENERATE_TAGS, "kGenerateRegion"))
    )


def _scope_items(item_list: dict | None):
    """The items of one generate scope, with ``generate`` regions opened.

    A ``generate … endgenerate`` region is not a scope (LRM § 27.3):
    its items belong to the enclosing one, and so does their
    ``genblk`` numbering.
    """
    if item_list is None:
        return
    for child in item_list.get("children", ()) or ():
        if not isinstance(child, dict):
            continue
        if child.get("tag") == "kGenerateRegion":
            yield from _scope_items(_first_child_with_tag(child, "kGenerateItemList"))
        else:
            yield child


def _scope_params(
    item_list: dict | None, *, file: str, offsets: OffsetIndex, source: bytes
) -> tuple[Parameter, ...]:
    """``localparam`` / body ``parameter`` declarations of one scope."""
    return tuple(
        _param_from_decl(item, file=file, offsets=offsets, source=source)
        for item in _scope_items(item_list)
        if item.get("tag") == "kParamDeclaration"
    )


def _scope_nets(
    item_list: dict | None, *, file: str, offsets: OffsetIndex, source: bytes
) -> tuple[str, ...]:
    """Names of the nets/variables one generate scope declares."""
    names: list[str] = []
    for item in _scope_items(item_list):
        tag = item.get("tag")
        if tag == "kDataDeclaration":
            decls = _var_decls(item, file=file, offsets=offsets, source=source)
        elif tag == "kNetDeclaration":
            decls = _net_decls(item, file=file, offsets=offsets, source=source)
        else:
            continue
        names.extend(d.name for d in decls)
    return tuple(dict.fromkeys(names))


def _taken_genblk_names(item_list: dict | None) -> set[str]:
    """Explicit ``genblk<N>``-shaped names in a scope, for collision padding.

    Over-approximates (any matching identifier inside the scope's own
    items counts), which can only add a padding zero, never drop one.
    """
    taken: set[str] = set()
    for item in _scope_items(item_list):
        for sym in _iter_nodes_with_tag(item, "SymbolIdentifier"):
            text = sym.get("text", "")
            if _GENBLK_RE.match(text):
                taken.add(text)
    return taken


def _default_label(number: int, taken: set[str]) -> str:
    """``genblk<N>``, zero-padded past any explicit name it would shadow."""
    digits = str(number)
    while f"genblk{digits}" in taken:
        digits = "0" + digits
    return f"genblk{digits}"


def _generate_scope(
    item_list: dict | None, *, file: str, offsets: OffsetIndex, source: bytes
) -> tuple[GenerateItem, ...]:
    """One scope's instances, assigns and generate constructs, in order."""
    out: list[GenerateItem] = []
    taken = _taken_genblk_names(item_list)
    number = 0
    for item in _scope_items(item_list):
        tag = item.get("tag")
        if tag == "kDataDeclaration":
            out.extend(
                _instances_from_decl(item, file=file, offsets=offsets, source=source)
            )
        elif tag in ("kContinuousAssignmentStatement", "kNetDeclaration"):
            out.extend(
                _extract_assigns(item, file=file, offsets=offsets, source=source)
            )
        elif tag in _GENERATE_TAGS:
            number += 1
            default = _default_label(number, taken)
            construct = (
                _loop_template(item, default, file=file, offsets=offsets, source=source)
                if tag == "kLoopGenerateConstruct"
                else _conditional_template(
                    item, default, file=file, offsets=offsets, source=source
                )
            )
            if construct is not None:
                out.append(construct)
    return tuple(out)


def _block_parts(
    body: dict, *, file: str, offsets: OffsetIndex, source: bytes
) -> tuple[
    str | None, tuple[GenerateItem, ...], tuple[str, ...], tuple[Parameter, ...]
]:
    """``(label, items, local nets, local params)`` of one generate block.

    A ``begin : label … end`` block is a ``kGenerateBlock``; a body
    written without ``begin``/``end`` is a single item, which is a
    (necessarily unnamed) generate block of its own.
    """
    if body.get("tag") == "kGenerateBlock":
        label: str | None = None
        begin = _first_child_with_tag(body, "kBegin")
        if begin is not None:
            lbl = _first_child_with_tag(begin, "kLabel")
            sym = _find_first_sym(lbl) if lbl is not None else None
            if sym is not None:
                label = sym["text"]
        item_list = _first_child_with_tag(body, "kGenerateItemList")
    else:
        label = None
        item_list = {"tag": "kGenerateItemList", "children": [body]}
    return (
        label,
        _generate_scope(item_list, file=file, offsets=offsets, source=source),
        _scope_nets(item_list, file=file, offsets=offsets, source=source),
        _scope_params(item_list, file=file, offsets=offsets, source=source),
    )


def _span_location(node: dict, file: str, offsets: OffsetIndex) -> SourceLocation:
    span = _node_span(node)
    if span is None:
        return SourceLocation(file=file)
    return _location(file, offsets, span[0], span[1])


def _paren_expr_text(paren: dict | None, source: bytes) -> str | None:
    """The expression inside a ``kParenGroup``, verbatim."""
    if paren is None:
        return None
    expr = _first_child_with_tag(paren, "kExpression")
    if expr is not None:
        return _source_slice(expr, source)
    text = _source_slice(paren, source)
    if text is None:
        return None
    return text.strip()[1:-1].strip() or None


def _loop_template(
    node: dict, default: str, *, file: str, offsets: OffsetIndex, source: bytes
) -> GenerateLoop | None:
    """A ``kLoopGenerateConstruct`` as a :class:`GenerateLoop`."""
    header = _first_child_with_tag(node, "kLoopHeader")
    paren = _first_child_with_tag(header, "kParenGroup") if header else None
    spec = _first_child_with_tag(paren, "kForSpec") if paren else None
    if spec is None:
        return None
    parts = {
        tag: _first_child_with_tag(spec, tag)
        for tag in ("kForInitialization", "kForCondition", "kForStepList")
    }
    texts = {
        tag: (_source_slice(n, source) if n is not None else None)
        for tag, n in parts.items()
    }
    init = texts["kForInitialization"]
    cond = texts["kForCondition"]
    step = texts["kForStepList"]
    if init is None or cond is None or step is None:
        return None
    genvar = re.match(r"\s*(?:genvar\s+)?([A-Za-z_][A-Za-z0-9_$]*)", init)
    if genvar is None:
        return None
    body = next(
        (
            c
            for c in node.get("children", ()) or ()
            if isinstance(c, dict) and c.get("tag") not in (None, "kLoopHeader")
        ),
        None,
    )
    if body is None:
        return None
    label, items, nets, params = _block_parts(
        body, file=file, offsets=offsets, source=source
    )
    return GenerateLoop(
        label=label or default,
        genvar=genvar.group(1),
        init_text=init,
        cond_text=cond,
        step_text=step,
        items=items,
        local_nets=nets,
        local_params=params,
        location=_span_location(node, file, offsets),
    )


def _first_item_child(node: dict | None) -> dict | None:
    """The first CST-node (``k…``) child — a generate body's item."""
    if node is None:
        return None
    for child in node.get("children", ()) or ():
        if isinstance(child, dict) and str(child.get("tag", "")).startswith("k"):
            return child
    return None


def _if_arms(
    node: dict, guards: tuple[tuple[str, bool], ...], source: bytes
) -> list[tuple[tuple[tuple[str, bool], ...], dict]]:
    """Flattened ``(guards, body)`` arms of a ``kConditionalGenerateConstruct``."""
    clause = _first_child_with_tag(node, "kGenerateIfClause")
    if clause is None:
        return []
    header = _first_child_with_tag(clause, "kGenerateIfHeader")
    gen_if = _first_child_with_tag(header, "kGenerateIf") if header else None
    cond = _paren_expr_text(
        _first_child_with_tag(gen_if, "kParenGroup") if gen_if else None, source
    )
    if cond is None:
        return []
    arms = _nested_arms(
        _first_item_child(_first_child_with_tag(clause, "kGenerateIfBody")),
        guards + ((cond, True),),
        source,
    )
    else_clause = _first_child_with_tag(node, "kGenerateElseClause")
    if else_clause is not None:
        arms += _nested_arms(
            _first_item_child(_first_child_with_tag(else_clause, "kGenerateElseBody")),
            guards + ((cond, False),),
            source,
        )
    return arms


def _case_arms(
    node: dict, guards: tuple[tuple[str, bool], ...], source: bytes
) -> list[tuple[tuple[tuple[str, bool], ...], dict]]:
    """Flattened ``(guards, body)`` arms of a ``kCaseGenerateConstruct``.

    Item ``k`` is ``(E) == (label)`` (any of its labels) with every
    earlier item's match false; ``default`` is every item's match false.
    """
    selector = _paren_expr_text(_first_child_with_tag(node, "kParenGroup"), source)
    items = _first_child_with_tag(node, "kGenerateCaseItemList")
    if selector is None or items is None:
        return []
    matches: list[tuple[str, dict | None]] = []
    default_body: dict | None = None
    for item in items.get("children", ()) or ():
        if not isinstance(item, dict):
            continue
        body = next(
            (
                c
                for c in reversed(item.get("children", ()) or ())
                if isinstance(c, dict)
                and str(c.get("tag", "")).startswith("k")
                and c.get("tag") != "kExpressionList"
            ),
            None,
        )
        if item.get("tag") == "kGenerateDefaultItem":
            default_body = body
            continue
        if item.get("tag") != "kGenerateCaseItem":
            continue
        labels_node = _first_child_with_tag(item, "kExpressionList")
        labels = [
            text
            for e in (labels_node or {}).get("children", ()) or ()
            if isinstance(e, dict) and e.get("tag") == "kExpression"
            for text in [_source_slice(e, source)]
            if text is not None
        ]
        if not labels:
            continue
        match = " || ".join(f"({selector}) == ({label})" for label in labels)
        matches.append((match, body))
    arms: list[tuple[tuple[tuple[str, bool], ...], dict]] = []
    for index, (match, body) in enumerate(matches):
        prior = tuple((m, False) for m, _ in matches[:index])
        arms += _nested_arms(body, guards + prior + ((match, True),), source)
    if default_body is not None:
        prior = tuple((m, False) for m, _ in matches)
        arms += _nested_arms(default_body, guards + prior, source)
    return arms


def _nested_arms(
    body: dict | None, guards: tuple[tuple[str, bool], ...], source: bytes
) -> list[tuple[tuple[tuple[str, bool], ...], dict]]:
    """One arm, or a directly nested construct's arms (LRM § 27.5).

    A conditional arm whose only item is another ``if`` / ``case``
    written without ``begin``/``end`` is not a scope of its own: the
    inner construct's blocks belong to the outer one — which is what
    makes ``else if`` chains one construct.
    """
    if body is None:
        return []
    tag = body.get("tag")
    if tag == "kConditionalGenerateConstruct":
        return _if_arms(body, guards, source)
    if tag == "kCaseGenerateConstruct":
        return _case_arms(body, guards, source)
    return [(guards, body)]


def _conditional_template(
    node: dict, default: str, *, file: str, offsets: OffsetIndex, source: bytes
) -> GenerateConditional | None:
    """An ``if`` / ``case`` generate construct as a :class:`GenerateConditional`."""
    raw = (
        _if_arms(node, (), source)
        if node.get("tag") == "kConditionalGenerateConstruct"
        else _case_arms(node, (), source)
    )
    arms: list[GenerateArm] = []
    for guards, body in raw:
        label, items, nets, params = _block_parts(
            body, file=file, offsets=offsets, source=source
        )
        arms.append(
            GenerateArm(
                label=label or default,
                guards=guards,
                items=items,
                local_nets=nets,
                local_params=params,
            )
        )
    if not arms:
        return None
    return GenerateConditional(
        arms=tuple(arms), location=_span_location(node, file, offsets)
    )


def _extract_port_connections(
    gate: dict, *, file: str, offsets: OffsetIndex, source: bytes
) -> tuple[PortConnection, ...]:
    """Extract port connections (named, positional, and shorthand).

    Layouts::

        kGateInstance > kParenGroup > kPortActualList >
          # named:
          kActualNamedPort
            .
            SymbolIdentifier            <-- port name
            kParenGroup
              kExpression               <-- net expression

          # implicit `.port` shorthand:
          kActualNamedPort
            .
            SymbolIdentifier            <-- port name (no kParenGroup)

          # positional:
          kActualPositionalPort
            kExpression                 <-- net expression only

    For named connections we emit ``PortConnection(port_name, net_expr_text)``.
    For shorthand we leave ``net_expr_text`` empty — the renderer
    surfaces this as ``.port`` (matching source) rather than the
    canonical ``.port(port)`` form. Positional connections set
    ``port_name = None`` and store the bare net expression in
    ``net_expr_text``.
    """
    paren = _first_child_with_tag(gate, "kParenGroup")
    if paren is None:
        return ()
    actual_list = _first_child_with_tag(paren, "kPortActualList")
    if actual_list is None:
        return ()
    out: list[PortConnection] = []
    for entry in actual_list.get("children", ()) or ():
        if not isinstance(entry, dict):
            continue
        tag = entry.get("tag")
        if tag == "kActualNamedPort":
            conn = _named_port_connection(
                entry, file=file, offsets=offsets, source=source
            )
        elif tag == "kActualPositionalPort":
            conn = _positional_port_connection(
                entry, file=file, offsets=offsets, source=source
            )
        else:
            continue
        if conn is not None:
            out.append(conn)
    return tuple(out)


def _named_port_connection(
    entry: dict, *, file: str, offsets: OffsetIndex, source: bytes
) -> PortConnection | None:
    """Build a :class:`PortConnection` from a ``kActualNamedPort``.

    Handles both the full ``.port(expr)`` form and the implicit
    ``.port`` shorthand — distinguished by whether a ``kParenGroup``
    child is present.
    """
    port_name: str | None = None
    net_expr_text = ""
    loc_node: dict | None = None
    for child in entry.get("children", ()) or ():
        if not isinstance(child, dict):
            continue
        tag = child.get("tag")
        if tag == "SymbolIdentifier" and "text" in child:
            port_name = child["text"]
        elif tag == "kParenGroup":
            expr = _first_child_with_tag(child, "kExpression")
            if expr is not None:
                sliced = _source_slice(expr, source)
                if sliced is not None:
                    net_expr_text = sliced
                    loc_node = expr
    if port_name is None:
        return None
    span = _node_span(loc_node) if loc_node is not None else _node_span(entry)
    loc = (
        SourceLocation(file=file)
        if span is None
        else _location(file, offsets, span[0], span[1])
    )
    return PortConnection(
        port_name=port_name,
        net_expr_text=net_expr_text,
        location=loc,
    )


def _positional_port_connection(
    entry: dict, *, file: str, offsets: OffsetIndex, source: bytes
) -> PortConnection | None:
    """Build a :class:`PortConnection` from a ``kActualPositionalPort``.

    ``port_name`` is None — the positional index is implied by
    placement in :attr:`Instance.port_connections`. The net
    expression is stored verbatim.
    """
    expr = _first_child_with_tag(entry, "kExpression")
    if expr is None:
        return None
    net = _source_slice(expr, source)
    if net is None:
        return None
    span = _node_span(expr)
    loc = (
        SourceLocation(file=file)
        if span is None
        else _location(file, offsets, span[0], span[1])
    )
    return PortConnection(port_name=None, net_expr_text=net, location=loc)


def _extract_param_overrides(
    inst_type: dict, *, file: str, offsets: OffsetIndex, source: bytes
) -> tuple[ParameterOverride, ...]:
    """Extract named parameter overrides from a ``kInstantiationType``.

    Layout (named-override case)::

        kInstantiationType > kDataType > kLocalRoot > kUnqualifiedId
          SymbolIdentifier            <-- the module name
          kActualParameterList
            #
            kParenGroup
              kActualParameterByNameList
                kParamByName
                  .
                  SymbolIdentifier    <-- the parameter name
                  kParenGroup
                    kExpression       <-- the override value

    Positional overrides (``#(8, 16)``) use a sibling tag
    (``kActualParameterPositionalList``); each kExpression child
    becomes a :class:`ParameterOverride` with ``param_name=None``.
    """
    apl = next(_iter_nodes_with_tag(inst_type, "kActualParameterList"), None)
    if apl is None:
        return ()
    paren = _first_child_with_tag(apl, "kParenGroup")
    if paren is None:
        return ()
    positional_list = _first_child_with_tag(paren, "kActualParameterPositionalList")
    if positional_list is not None:
        return _positional_param_overrides(
            positional_list, file=file, offsets=offsets, source=source
        )
    by_name_list = _first_child_with_tag(paren, "kActualParameterByNameList")
    if by_name_list is None:
        return ()
    out: list[ParameterOverride] = []
    for entry in by_name_list.get("children", ()) or ():
        if not isinstance(entry, dict) or entry.get("tag") != "kParamByName":
            continue
        name = None
        value_text: str | None = None
        value_loc_node: dict | None = None
        # The .NAME(expr) layout: children are `.`, SymbolIdentifier,
        # kParenGroup. We walk explicitly rather than positional-index
        # so an unexpected child order doesn't crash.
        for child in entry.get("children", ()) or ():
            if not isinstance(child, dict):
                continue
            tag = child.get("tag")
            if tag == "SymbolIdentifier" and "text" in child:
                name = child["text"]
            elif tag == "kParenGroup":
                expr = _first_child_with_tag(child, "kExpression")
                if expr is not None:
                    value_text = _source_slice(expr, source)
                    value_loc_node = expr
        if name is None or value_text is None:
            continue
        span = (
            _node_span(value_loc_node)
            if value_loc_node is not None
            else _node_span(entry)
        )
        loc = (
            SourceLocation(file=file)
            if span is None
            else _location(file, offsets, span[0], span[1])
        )
        out.append(
            ParameterOverride(param_name=name, value_text=value_text, location=loc)
        )
    return tuple(out)


def _positional_param_overrides(
    positional_list: dict,
    *,
    file: str,
    offsets: OffsetIndex,
    source: bytes,
) -> tuple[ParameterOverride, ...]:
    """Build :class:`ParameterOverride` entries from a positional list.

    Layout::

        kActualParameterPositionalList
          kExpression   <-- override #0
          ,
          kExpression   <-- override #1
          …

    ``param_name`` is None — the positional index is implied by
    placement in :attr:`Instance.param_overrides`.
    """
    out: list[ParameterOverride] = []
    for entry in positional_list.get("children", ()) or ():
        if not isinstance(entry, dict) or entry.get("tag") != "kExpression":
            continue
        value_text = _source_slice(entry, source)
        if value_text is None:
            continue
        span = _node_span(entry)
        loc = (
            SourceLocation(file=file)
            if span is None
            else _location(file, offsets, span[0], span[1])
        )
        out.append(
            ParameterOverride(param_name=None, value_text=value_text, location=loc)
        )
    return tuple(out)


def _extract_assigns(
    module_node: dict, *, file: str, offsets: OffsetIndex, source: bytes
) -> tuple[Assign, ...]:
    """Extract continuous assignments from a ``kModuleDeclaration``.

    Layout (confirmed against ``verible-verilog-syntax
    --export_json --printtree``)::

        kContinuousAssignmentStatement
          assign
          kAssignmentList
            kNetVariableAssignment      <-- one per comma-separated target
              kLPValue                  <-- lhs
              =
              kExpression               <-- rhs
          ;

    Each ``kNetVariableAssignment`` yields its own :class:`Assign`, so
    ``assign a = b, c = d;`` produces two records.

    Unlike :func:`_extract_instances` this walks the whole declaration
    rather than only the top ``kModuleItemList``: assignments inside
    generate blocks alias nets exactly like flat ones do, and the
    connectivity analyzer only ever uses them as alias hints, so
    over-capturing is strictly better than dropping them. (A module
    that has generate constructs replaces this flat list with the
    unrolled one, genvars substituted — rtl-buddy-sch#209; this walk
    is also what reads one template item's assigns.) Net
    A *net* declaration with an initializer (``wire w = x;``) is a
    continuous assignment too — LRM 1800 § 6.7.1 — and Verible gives
    it its own shape (``kNetDeclarationAssignment``), so it is
    captured in a second pass. A *variable* initializer
    (``logic w = x;``, ``kTrailingAssign`` under
    ``kRegisterVariable``) is **not**: that is a one-time static
    initialisation, not a driver, and treating it as one would invent
    dataflow.
    """
    out: list[Assign] = []
    for stmt in _iter_nodes_with_tag(module_node, "kContinuousAssignmentStatement"):
        for nva in _iter_nodes_with_tag(stmt, "kNetVariableAssignment"):
            lpvalue = _first_child_with_tag(nva, "kLPValue")
            expr = _first_child_with_tag(nva, "kExpression")
            if lpvalue is None or expr is None:
                continue
            lhs_text = _source_slice(lpvalue, source)
            rhs_text = _source_slice(expr, source)
            if lhs_text is None or rhs_text is None:
                continue
            span = _node_span(nva)
            loc = (
                SourceLocation(file=file)
                if span is None
                else _location(file, offsets, span[0], span[1])
            )
            out.append(Assign(lhs_text=lhs_text, rhs_text=rhs_text, location=loc))
    for nda in _iter_nodes_with_tag(module_node, "kNetDeclarationAssignment"):
        sym = _first_child_with_tag(nda, "SymbolIdentifier")
        expr = _first_child_with_tag(nda, "kExpression")
        if sym is None or "text" not in sym or expr is None:
            continue
        rhs_text = _source_slice(expr, source)
        if rhs_text is None:
            continue
        span = _node_span(nda)
        loc = (
            SourceLocation(file=file)
            if span is None
            else _location(file, offsets, span[0], span[1])
        )
        out.append(Assign(lhs_text=sym["text"], rhs_text=rhs_text, location=loc))
    return tuple(out)


#: Procedural statements that assign a value: ``q <= d;``, ``c = a;``
#: (Verible reuses the continuous-assign tag for a blocking
#: assignment) and ``c += a;``. Each has a ``kLPValue`` and a
#: ``kExpression`` child.
_PROCEDURAL_ASSIGN_TAGS = (
    "kNonblockingAssignmentStatement",
    "kNetVariableAssignment",
    "kAssignModifyStatement",
)

#: Procedural constructs whose expressions steer a block rather than
#: being assigned: ``if (…)``, ``case (…)`` and its item labels, and
#: the ``for`` / ``while`` / ``do … while`` / ``repeat`` headers.
_PROCEDURAL_CONDITION_TAGS = (
    "kIfHeader",
    "kCaseStatement",
    "kCaseItem",
    "kLoopHeader",
    "kWhileLoopStatement",
    "kDoWhileLoopStatement",
    "kRepeatControl",
)


def _extract_procedural(
    module_node: dict, *, file: str, offsets: OffsetIndex, source: bytes
) -> tuple[tuple[Assign, ...], tuple[str, ...]]:
    """Extract assignments and conditions from ``always*`` blocks.

    Layout (confirmed against ``verible-verilog-syntax --printtree``)::

        kAlwaysStatement                      <- always / _ff / _comb / _latch
          kProceduralTimingControlStatement?  <- @(posedge clk …)
            kConditionalStatement
              kIfClause > kIfHeader > kParenGroup > kExpression   <- condition
                kIfBody > kNonblockingAssignmentStatement
                            kLPValue  <=  kExpression             <- assignment

    ``kNetVariableAssignment`` (a blocking ``=``) and
    ``kAssignModifyStatement`` (``+=``) have the same two children. A
    ``for`` initializer (``kForInitialization``) assigns its loop
    variable, named by a ``kLPValue`` or, when it declares one
    (``int i = 0``), by a bare ``SymbolIdentifier``.

    Conditions are the ``kParenGroup`` of an ``if`` / ``case`` /
    ``for`` / ``repeat`` header, the ``kExpression`` of a ``while`` /
    ``do … while``, and a case item's label list. The
    event control (``@(posedge clk_a)``) is neither: it says when the
    block runs, not what it reads.

    ``initial`` blocks, functions and tasks are not ``kAlwaysStatement``
    and are skipped — a one-time initialisation or a subroutine body is
    not a driver of the scope's nets.
    """
    assigns: list[Assign] = []
    conditions: list[str] = []
    for always in _iter_nodes_with_tag(module_node, "kAlwaysStatement"):
        for node in _iter_nodes_with_tags(
            always,
            _PROCEDURAL_ASSIGN_TAGS
            + _PROCEDURAL_CONDITION_TAGS
            + ("kForInitialization",),
        ):
            tag = node.get("tag")
            if tag in _PROCEDURAL_CONDITION_TAGS:
                if tag == "kCaseItem":
                    part = _first_child_with_tag(node, "kExpressionList")
                else:
                    part = _first_child_with_tag(
                        node, "kParenGroup"
                    ) or _first_child_with_tag(node, "kExpression")
                text = _source_slice(part, source) if part is not None else None
                if text:
                    conditions.append(text)
                continue
            lpvalue = _first_child_with_tag(node, "kLPValue")
            expr = _first_child_with_tag(node, "kExpression")
            if expr is None:
                continue
            if lpvalue is not None:
                lhs_text = _source_slice(lpvalue, source)
            else:
                sym = _first_child_with_tag(node, "SymbolIdentifier")
                lhs_text = sym.get("text") if sym is not None else None
            rhs_text = _source_slice(expr, source)
            if not lhs_text or rhs_text is None:
                continue
            span = _node_span(node)
            loc = (
                SourceLocation(file=file)
                if span is None
                else _location(file, offsets, span[0], span[1])
            )
            assigns.append(Assign(lhs_text=lhs_text, rhs_text=rhs_text, location=loc))
    return tuple(assigns), tuple(conditions)


#: Tags whose *direct* children are module-body items. Restricting the
#: net-declaration walk to these keeps procedural temporaries out: a
#: ``logic`` declared inside a function, task or ``always`` block
#: hangs off ``kBlockItemStatementList``, which is not in this set.
_ITEM_LIST_TAGS = ("kModuleItemList", "kGenerateItemList")


def _extract_net_decls(
    module_node: dict,
    *,
    file: str,
    offsets: OffsetIndex,
    source: bytes,
    port_names: set[str],
) -> tuple[NetDecl, ...]:
    """Extract module-body net/variable declarations.

    Two CST shapes, confirmed against ``verible-verilog-syntax
    --printtree``.

    Variable style (``logic``/``reg``/``bit``/``int``/a typedef'd
    name) reuses the ``kDataDeclaration`` node that also carries
    module instantiations::

        kDataDeclaration
          kInstantiationBase
            kInstantiationType
              kDataType                 <- "logic [18:0]"  (type_text)
            kGateInstanceRegisterVariableList
              kRegisterVariable         <- one per declarator
                SymbolIdentifier
                kTrailingAssign?        <- ``logic [7:0] f = 8'h5;``

    An instantiation puts ``kGateInstance`` in that same list rather
    than ``kRegisterVariable``, which is exactly what separates the
    two — no name heuristics needed.

    Net style (``wire``/``tri``/…) has its own tag, and hangs the
    packed range off a *second* type node::

        kNetDeclaration
          kDataType                     <- "wire"
          kDataTypeImplicitIdDimensions <- "signed [7:0]", absent when unranged
          kNetVariableDeclarationAssign
            kNetVariable                <- one per declarator, no initializer
              SymbolIdentifier
            kNetDeclarationAssignment   <- one per declarator, with initializer
              SymbolIdentifier

    ``type_text`` is the contiguous source slice spanning both type
    nodes, so it reads back verbatim (``"wire signed [7:0]"``).

    Declarations inside generate bodies are captured too — the
    connectivity analyzer already treats generate-scoped assigns as
    flat, and it resolves a name declared at two widths as *unknown*,
    so a shadowing declaration degrades rather than lies.

    ``port_names`` drops the redundant re-declaration of a port a
    non-ANSI header allows (``module m(a); input [3:0] a; wire [3:0]
    a;``): the port is already :attr:`Module.ports`, and one name
    should not be two records.
    """
    out: list[NetDecl] = []
    for item_list in _iter_nodes_with_tags(module_node, _ITEM_LIST_TAGS):
        for item in item_list.get("children", ()) or ():
            if not isinstance(item, dict):
                continue
            tag = item.get("tag")
            if tag == "kDataDeclaration":
                out.extend(_var_decls(item, file=file, offsets=offsets, source=source))
            elif tag == "kNetDeclaration":
                out.extend(_net_decls(item, file=file, offsets=offsets, source=source))
    return tuple(decl for decl in out if decl.name not in port_names)


def _var_decls(
    decl: dict, *, file: str, offsets: OffsetIndex, source: bytes
) -> list[NetDecl]:
    """Variable-style declarators under one ``kDataDeclaration``."""
    base = _first_child_with_tag(decl, "kInstantiationBase")
    if base is None:
        return []
    inst_type = _first_child_with_tag(base, "kInstantiationType")
    var_list = _first_child_with_tag(base, "kGateInstanceRegisterVariableList")
    if inst_type is None or var_list is None:
        return []
    dtype = _first_child_with_tag(inst_type, "kDataType")
    type_text = _source_slice(dtype, source) if dtype is not None else None
    out: list[NetDecl] = []
    for var in var_list.get("children", ()) or ():
        if not isinstance(var, dict) or var.get("tag") != "kRegisterVariable":
            continue
        entry = _net_decl_from_declarator(
            var, type_text=type_text, file=file, offsets=offsets
        )
        if entry is not None:
            out.append(entry)
    return out


def _net_decls(
    decl: dict, *, file: str, offsets: OffsetIndex, source: bytes
) -> list[NetDecl]:
    """Net-style declarators under one ``kNetDeclaration``."""
    dtype = _first_child_with_tag(decl, "kDataType")
    type_text = None
    if dtype is not None:
        span = _node_span(dtype)
        implicit = _first_child_with_tag(decl, "kDataTypeImplicitIdDimensions")
        dims_span = _node_span(implicit) if implicit is not None else None
        if span is not None:
            end = dims_span[1] if dims_span is not None else span[1]
            type_text = source[span[0] : end].decode("utf-8", errors="replace")
    var_list = _first_child_with_tag(decl, "kNetVariableDeclarationAssign")
    if var_list is None:
        return []
    out: list[NetDecl] = []
    for var in var_list.get("children", ()) or ():
        if not isinstance(var, dict):
            continue
        if var.get("tag") not in ("kNetVariable", "kNetDeclarationAssignment"):
            continue
        entry = _net_decl_from_declarator(
            var, type_text=type_text, file=file, offsets=offsets
        )
        if entry is not None:
            out.append(entry)
    return out


def _net_decl_from_declarator(
    var: dict, *, type_text: str | None, file: str, offsets: OffsetIndex
) -> NetDecl | None:
    """Build one :class:`NetDecl` from a declarator node, or ``None``."""
    sym = _first_child_with_tag(var, "SymbolIdentifier")
    if sym is None or "text" not in sym:
        return None
    span = _node_span(sym)
    loc = (
        SourceLocation(file=file)
        if span is None
        else _location(file, offsets, span[0], span[1])
    )
    return NetDecl(name=sym["text"], type_text=type_text, location=loc)


def _extract_dpi_functions(
    module_node: dict, *, file: str, offsets: OffsetIndex, source: bytes
) -> tuple[DpiFunction, ...]:
    """Extract DPI import/export items from a ``kModuleDeclaration``. (#127.)

    Layouts (confirmed against Verible's CST)::

        kModuleItemList
          kDPIImportItem
            import
            TK_StringLiteral            <-- "DPI-C"
            SymbolIdentifier            <-- C alias (only in the
            =                               ``c_name = function ...`` form)
            context | pure              <-- optional qualifier
            kFunctionPrototype | kTaskPrototype
              kFunctionHeader | kTaskHeader
                kUnqualifiedId
                  SymbolIdentifier      <-- the SV name
          kDPIExportItem
            export
            TK_StringLiteral
            SymbolIdentifier            <-- C alias (optional, as above)
            =
            kFunctionPrototype ...

    ``c_symbol`` is the alias when present, else the SV name — that is
    the name the C side links against either way. Only direct children
    of the module's ``kModuleItemList`` are walked (same flat-scope
    rule as :func:`_extract_instances`); DPI items in generate blocks
    or at compilation-unit scope are out of scope.
    """
    item_list = _first_child_with_tag(module_node, "kModuleItemList")
    if item_list is None:
        return ()
    out: list[DpiFunction] = []
    for child in item_list.get("children", ()) or ():
        if not isinstance(child, dict):
            continue
        tag = child.get("tag")
        if tag not in ("kDPIImportItem", "kDPIExportItem"):
            continue
        direction = "import" if tag == "kDPIImportItem" else "export"
        dpi = _dpi_from_item(
            child, direction=direction, file=file, offsets=offsets, source=source
        )
        if dpi is not None:
            out.append(dpi)
    if not out:
        return ()
    imported = {d.sv_name for d in out if d.direction == "import"}
    sites = _dpi_call_sites(module_node, imported, file=file, offsets=offsets)
    return tuple(
        DpiFunction(
            sv_name=d.sv_name,
            c_symbol=d.c_symbol,
            direction=d.direction,
            signature=d.signature,
            location=d.location,
            call_sites=tuple(sites.get(d.sv_name, ()))
            if d.direction == "import"
            else (),
        )
        for d in out
    )


def _dpi_from_item(
    item: dict,
    *,
    direction: str,
    file: str,
    offsets: OffsetIndex,
    source: bytes,
) -> DpiFunction | None:
    """Build one :class:`DpiFunction` from a kDPIImportItem/kDPIExportItem."""
    alias: str | None = None
    for child in item.get("children", ()) or ():
        # The C alias is the only *direct-child* SymbolIdentifier; the
        # SV name always sits inside the prototype's kUnqualifiedId.
        if (
            isinstance(child, dict)
            and child.get("tag") == "SymbolIdentifier"
            and "text" in child
        ):
            alias = child["text"]
            break
    proto = _first_child_with_tag(item, "kFunctionPrototype") or _first_child_with_tag(
        item, "kTaskPrototype"
    )
    if proto is None:
        return None
    header = _first_child_with_tag(proto, "kFunctionHeader") or _first_child_with_tag(
        proto, "kTaskHeader"
    )
    if header is None:
        return None
    unqualified = _first_child_with_tag(header, "kUnqualifiedId")
    sym = (
        _first_child_with_tag(unqualified, "SymbolIdentifier")
        if unqualified is not None
        else None
    )
    if sym is None or "text" not in sym:
        return None
    sv_name = sym["text"]
    signature = _source_slice(proto, source)
    if signature is not None:
        # The aliased form parses the trailing `;` *inside* the
        # prototype's header; the plain form leaves it a sibling.
        # Normalize so the signature reads the same either way.
        signature = signature.rstrip().rstrip(";").rstrip()
    span = _node_span(sym)
    loc = (
        SourceLocation(file=file)
        if span is None
        else _location(file, offsets, span[0], span[1])
    )
    return DpiFunction(
        sv_name=sv_name,
        c_symbol=alias if alias is not None else sv_name,
        direction=direction,  # type: ignore[arg-type]
        signature=signature,
        location=loc,
    )


def _dpi_call_sites(
    module_node: dict,
    imported: set[str],
    *,
    file: str,
    offsets: OffsetIndex,
) -> dict[str, list[SourceLocation]]:
    """Call sites of imported DPI functions inside ``module_node``.

    Best-effort by charter (#127): a ``kFunctionCall`` whose callee is
    a *simple* reference (a lone ``kLocalRoot``, no hierarchy suffix)
    naming an imported DPI function counts; anything the CST doesn't
    make cheap — hierarchical calls, calls through a package scope —
    is skipped silently.
    """
    sites: dict[str, list[SourceLocation]] = {}
    if not imported:
        return sites
    for call in _iter_nodes_with_tag(module_node, "kFunctionCall"):
        base = _first_child_with_tag(call, "kReferenceCallBase")
        if base is None:
            continue
        ref = _first_child_with_tag(base, "kReference")
        if ref is None:
            continue
        ref_children = [c for c in ref.get("children", ()) or () if isinstance(c, dict)]
        if len(ref_children) != 1 or ref_children[0].get("tag") != "kLocalRoot":
            continue
        unqualified = _first_child_with_tag(ref_children[0], "kUnqualifiedId")
        sym = (
            _first_child_with_tag(unqualified, "SymbolIdentifier")
            if unqualified is not None
            else None
        )
        if sym is None or sym.get("text") not in imported:
            continue
        span = _node_span(sym)
        loc = (
            SourceLocation(file=file)
            if span is None
            else _location(file, offsets, span[0], span[1])
        )
        sites.setdefault(sym["text"], []).append(loc)
    return sites


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


def _iter_nodes_with_tags(node: dict | None, tags: tuple[str, ...]):
    """:func:`_iter_nodes_with_tag` over a set of tags, in document order.

    Pre-order, so an outer ``kModuleItemList`` is visited before the
    ``kGenerateItemList`` nested inside it — deterministic output
    without a sort (AGENTS.md § Development rules).
    """
    if node is None:
        return
    if isinstance(node, dict):
        if node.get("tag") in tags:
            yield node
        for child in node.get("children", ()) or ():
            yield from _iter_nodes_with_tags(child, tags)


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
