"""CST-cache shape guard for the Verible frontend (rtl-buddy-sch#186).

The CST cache is shared by every rtl-buddy tool on the machine, keyed
only on the Verible version and the file's content hash. Older
rtl-buddy-xeno releases stored Verible's raw ``--export_json``
envelope (``{"<path>": {"tree": ...}}``) where view stores the bare
tree, and view read such an entry as a tree with no modules — the
export then died with ``Known modules: []``.

Two defences are pinned here:

- the cache unwraps an envelope entry, so a design whose files were
  first parsed by xeno still yields its modules (real Verible run);
- a CST walk that finds no module while the source plainly declares
  one raises :class:`VeribleParseError` instead of returning an empty
  table. :func:`verible._find_module_declaration` is the conservative
  source check behind it, exercised hermetically against the shapes
  that must *not* trip it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from rtl_buddy_view import cst_cache
from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.frontend import verible

_have_verible = pytest.mark.skipif(
    find_binary("verible-verilog-syntax") is None,
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)

COUNTER_SV = """\
module m(input logic clk, output logic [3:0] q);
  always_ff @(posedge clk) q <= q + 1'b1;
endmodule
"""


@pytest.fixture(autouse=True)
def _isolate_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("RTL_BUDDY_CACHE_DIR", str(cache_dir))
    monkeypatch.delenv("RTL_BUDDY_NO_CACHE", raising=False)
    monkeypatch.delenv("RTL_BUDDY_VIEW_NO_CACHE", raising=False)
    return cache_dir


def _seed(src: Path, payload: object) -> Path:
    """Write ``payload`` as the cache entry view will look up for ``src``."""
    entry = cst_cache._cache_path_for(verible.locate_binary(), src, None)
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(json.dumps(payload))
    return entry


# --- envelope entries are read through (real Verible) -------------------------


@_have_verible
def test_xeno_envelope_entry_still_yields_modules(tmp_path: Path) -> None:
    """The issue's repro: the same content parsed first under a xeno temp
    path, its raw envelope cached; view must still see module ``m``."""
    src = tmp_path / "m.sv"
    src.write_text(COUNTER_SV)
    xeno_copy = tmp_path / "rtl-buddy-xeno" / "sv-0123456789abcdef.sv"
    xeno_copy.parent.mkdir()
    xeno_copy.write_text(COUNTER_SV)
    proc = subprocess.run(
        [str(verible.locate_binary()), "--export_json", "--printtree", str(xeno_copy)],
        check=True,
        capture_output=True,
        text=True,
    )
    envelope = json.loads(proc.stdout)
    assert list(envelope) == [str(xeno_copy)]
    entry = _seed(src, envelope)

    table = verible.parse([src])

    assert set(table.modules_by_name) == {"m"}
    assert [p.name for p in table.modules_by_name["m"].ports] == ["clk", "q"]
    # The entry is healed to the bare tree view itself would store.
    assert json.loads(entry.read_text()) == envelope[str(xeno_copy)]["tree"]


# --- the loud error when a walk misses a declared module ---------------------


@_have_verible
def test_wrong_shape_cst_raises_instead_of_empty_table(tmp_path: Path) -> None:
    """A CST that holds no module for a file that declares one is an
    error, not an empty table — whatever shape the bad entry has."""
    src = tmp_path / "m.sv"
    src.write_text("// counter\n" + COUNTER_SV)
    _seed(src, {"tag": "kDescriptionList", "children": []})

    with pytest.raises(verible.VeribleParseError) as exc:
        verible.parse([src])

    msg = str(exc.value)
    assert "declares module 'm' at line 2" in msg
    assert "RTL_BUDDY_NO_CACHE=1" in msg


@_have_verible
def test_package_and_interface_only_files_parse_clean(tmp_path: Path) -> None:
    """Files that legitimately hold no module still parse to an empty
    module table — the guard only fires on a declared module."""
    pkg = tmp_path / "p.sv"
    pkg.write_text(
        "// module m is defined elsewhere\n"
        'package p;\n  localparam string S = "module x;";\nendpackage\n'
    )
    iface = tmp_path / "i.sv"
    iface.write_text("interface bus_if;\n  logic v;\nendinterface\n")

    table = verible.parse([pkg, iface])

    assert table.modules_by_name == {}
    assert set(table.interfaces_by_name) == {"bus_if"}


# --- the source check itself (hermetic) ---------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("module a; endmodule\n", (1, "a"), id="plain"),
        pytest.param("\n\n  module b #(parameter W=1) ();\n", (3, "b"), id="indented"),
        pytest.param("(* keep *) module k; endmodule\n", (1, "k"), id="attribute"),
        pytest.param("module automatic ma; endmodule\n", (1, "ma"), id="lifetime"),
        pytest.param(
            '/* module x; */\nlogic s = "module y;";\nmodule z; endmodule\n',
            (3, "z"),
            id="after-comment-and-string",
        ),
        pytest.param(
            "`ifdef A\nmodule a; endmodule\n`endif\nmodule b; endmodule\n",
            (4, "b"),
            id="after-conditional-block",
        ),
    ],
)
def test_find_module_declaration_hits(text: str, expected: tuple[int, str]) -> None:
    assert verible._find_module_declaration(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("package p; endpackage\n", id="package"),
        pytest.param("interface i; endinterface\n", id="interface"),
        pytest.param("// module a;\n", id="line-comment"),
        pytest.param("/*\nmodule a;\nendmodule\n*/\n", id="block-comment"),
        pytest.param('string s = "\\"module a;";\n', id="string-with-escape"),
        pytest.param("`ifdef FOO\nmodule a; endmodule\n`endif\n", id="ifdef"),
        pytest.param(
            "`ifndef G\n`define G\nmodule g; endmodule\n`endif\n",
            id="include-guard",
        ),
        pytest.param("`define M(n) module n; endmodule\n`M(x)\n", id="define"),
        pytest.param(
            "`define M(n) \\\nmodule n; \\\nendmodule\n`M(x)\n",
            id="define-continuation",
        ),
        pytest.param("module `NAME (input a);\nendmodule\n", id="macro-name"),
        pytest.param("macromodule mm; endmodule\n", id="macromodule"),
        pytest.param("extern module e(input logic a);\n", id="extern"),
        pytest.param("endmodule\n", id="endmodule"),
    ],
)
def test_find_module_declaration_misses(text: str) -> None:
    assert verible._find_module_declaration(text) is None
