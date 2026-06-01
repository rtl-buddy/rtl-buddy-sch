"""Hierarchy graph builder.

Pure-data layer. Given a :class:`rtl_buddy_view.extractor.ModuleTable`
and a top-module name, recursively resolves instances into a tree (or
DAG, when the same module is instantiated more than once) of
:class:`HierNode` records. Every node carries its instance path,
referenced module, parameter overrides, and the originating
:class:`Instance` so renderers and the query API can recover both
source-level and structural information.

Phase 1 scope:
- Top-down, single-top resolution
- Multiple instances of the same module (shared definition, distinct
  instance paths)
- Unresolved modules (libraries, blackboxes) flagged via
  :attr:`HierNode.is_blackbox`; not dropped silently
- No generate / parameterized fallback yet — that lands in Phase 2 via
  the slang frontend
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rtl_buddy_view.extractor import Instance, Module, ModuleTable


class HierarchyError(ValueError):
    pass


@dataclass(frozen=True)
class HierNode:
    """One node in the elaborated hierarchy tree.

    ``instance_path`` is the dot-separated absolute path from the top
    (e.g. ``"top.u_fifo.u_wr_ptr"``). The top node has
    ``instance_path == top.name`` and ``instance is None``.
    """

    instance_path: str
    module_name: str
    instance: Instance | None  # None at the top node
    module: Module | None  # None when is_blackbox
    is_blackbox: bool
    children: tuple["HierNode", ...] = field(default_factory=tuple)


def build_hierarchy(table: ModuleTable, top: str) -> HierNode:
    """Build the hierarchy graph rooted at ``top``.

    Raises :class:`HierarchyError` if ``top`` is not in the table.
    Unresolved child module names (modules referenced from an
    instance but not defined in ``table``) become blackbox leaves.
    Repeated child names within the same parent are allowed — each
    becomes a distinct :class:`HierNode` keyed by its instance path.
    """
    if top not in table.modules_by_name:
        known = sorted(table.modules_by_name)
        raise HierarchyError(f"top module {top!r} not found. Known modules: {known}")
    return _build(table, table.modules_by_name[top], path=top, instance=None)


def _subtree_contains(table: ModuleTable, start: str, target: str) -> bool:
    """True when ``target`` appears anywhere in ``start``'s instance
    subtree (cycle-safe)."""
    seen: set[str] = set()
    stack = [start]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        module = table.modules_by_name.get(name)
        if module is None:
            continue
        for inst in module.instances:
            if inst.module_name == target:
                return True
            stack.append(inst.module_name)
    return False


def find_tb_top(table: ModuleTable, dut_top: str) -> str | None:
    """Recover the testbench top module for a TB-rooted render.

    A caller's ``--tb-top`` hint is often wrong: the testbench *config*
    name (e.g. ``tb_apb``) frequently differs from the actual top
    module, which is commonly just ``tb_top``. This finds it from the
    elaborated design instead of trusting the name.

    A *root* is a module instantiated by no other module in the table.
    Preference order:

    - When ``dut_top`` is a real module: the unique root whose subtree
      contains it (the testbench wrapping the DUT). ``None`` if there's
      no such root (``dut_top`` is itself the sole top — no wrapping TB)
      or more than one (ambiguous; caller should keep its explicit
      flag).
    - When ``dut_top`` isn't a module (e.g. the "DUT" is an SV
      interface, which the module table doesn't carry): the sole root
      module, if there's exactly one.

    Returns the module name, or ``None`` when it can't be determined
    unambiguously — callers then fall back to their original behaviour
    (and surface the usual "module not found" error).
    """
    instantiated = {
        inst.module_name
        for module in table.modules_by_name.values()
        for inst in module.instances
    }
    roots = [name for name in table.modules_by_name if name not in instantiated]

    if dut_top in table.modules_by_name:
        containing = [
            r for r in roots if r != dut_top and _subtree_contains(table, r, dut_top)
        ]
        return containing[0] if len(containing) == 1 else None

    # ``dut_top`` isn't a module — fall back to the sole root module.
    return roots[0] if len(roots) == 1 else None


def _build(
    table: ModuleTable,
    module: Module,
    *,
    path: str,
    instance: Instance | None,
) -> HierNode:
    children: list[HierNode] = []
    for inst in module.instances:
        child_path = f"{path}.{inst.name}"
        child_module = table.modules_by_name.get(inst.module_name)
        if child_module is None:
            table.unresolved.add(inst.module_name)
            children.append(
                HierNode(
                    instance_path=child_path,
                    module_name=inst.module_name,
                    instance=inst,
                    module=None,
                    is_blackbox=True,
                    children=(),
                )
            )
            continue
        children.append(_build(table, child_module, path=child_path, instance=inst))
    return HierNode(
        instance_path=path,
        module_name=module.name,
        instance=instance,
        module=module,
        is_blackbox=False,
        children=tuple(children),
    )
