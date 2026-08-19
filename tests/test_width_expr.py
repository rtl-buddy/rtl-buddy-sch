"""Unit tests for the algebraic width layer (epic #163).

Two things are on trial here. The first is *safety*: the evaluator
reads unelaborated source text, so the garbage and malicious cases
below are the point of the module, not an afterthought — every one of
them must come back as an abstention rather than an exception, a hang,
or a number. The second is *silence*: tier 2 prints an expression only
for the one clean pattern it documents, because a cryptic label on a
wire is worse than no label.
"""

from __future__ import annotations

import pytest

from rtl_buddy_view.width_expr import (
    MAX_EXPR_LEN,
    eval_int_expr,
    leading_range,
    normalize_expr,
    simplify_range,
    substitute_params,
    sum_width_exprs,
    width_expr_of,
    width_of,
)

# --- the evaluator: what it folds --------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0", 0),
        ("19", 19),
        ("19-1", 18),
        ("8 - 1", 7),
        ("2*4", 8),
        ("2*4-1", 7),
        ("(3+1)*2-1", 7),
        ("-3", -3),
        ("--3", 3),
        ("-(2+1)", -3),
        ("1+2*3", 7),
        ("  7  ", 7),
    ],
)
def test_eval_folds_the_arithmetic_it_supports(text: str, expected: int) -> None:
    assert eval_int_expr(text) == expected


def test_eval_respects_precedence_and_parentheses() -> None:
    """The one property a hand-rolled parser most easily gets wrong."""
    assert eval_int_expr("2+3*4") == 14
    assert eval_int_expr("(2+3)*4") == 20
    assert eval_int_expr("10-2-3") == 5  # left-associative, not 11


# --- the evaluator: what it refuses ------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        "W",  # an unresolved identifier is not a number
        "W-1",
        "$clog2(8)",  # system function
        "8/2",  # division
        "2**8",  # exponentiation
        "2 ** 8",
        "8>>1",  # shifts
        "a ? 1 : 2",  # ternary
        "4'd8",  # sized literal
        "0x10",  # not SystemVerilog, and not decimal
        "1_000",  # underscore separators
        "1.5",  # reals
        "(1+2",  # unbalanced
        "1+2)",
        "()",
        "+5",  # unary plus is not in the grammar
        "1 2",  # two adjacent atoms must never silently concatenate
        "12 34",
        "*3",
        "3*",
        "3+",
        "__import__('os').system('echo pwned')",
        "eval('1+1')",
        "1;import os",
    ],
)
def test_eval_abstains_on_anything_outside_the_grammar(text: str | None) -> None:
    assert eval_int_expr(text) is None


def test_eval_never_concatenates_across_whitespace() -> None:
    """``"1 2"`` must not become 12 — the classic normalization bug."""
    assert eval_int_expr("1 2") is None
    assert normalize_expr("1 2") is None


def test_eval_survives_pathological_input() -> None:
    """No RecursionError, no hang: a pure analyzer may only abstain."""
    assert eval_int_expr("(" * 5000 + "1" + ")" * 5000) is None
    assert eval_int_expr("(" * 40 + "1" + ")" * 40) is None
    assert eval_int_expr("-" * 40 + "1") is None  # unary-minus chain
    assert eval_int_expr("(1 2)") is None  # a paren that never closes cleanly
    assert eval_int_expr("1+" * 500 + "1") is None  # over the length cap
    assert eval_int_expr("9" * 300) is None
    # Just inside the depth cap still folds, so the cap is a cap and
    # not a blanket refusal.
    assert eval_int_expr("(" * 20 + "1" + ")" * 20) == 1


def test_eval_of_a_long_but_legal_expression_is_capped() -> None:
    assert eval_int_expr("1+1") == 2
    assert eval_int_expr("+".join(["1"] * 200)) is None


# --- normalization ------------------------------------------------------------


def test_normalize_collapses_whitespace_around_operators() -> None:
    assert normalize_expr("A + B") == "A+B"
    assert normalize_expr("  PTR_W  ") == "PTR_W"
    assert normalize_expr("(A + B) * 2") == "(A+B)*2"


def test_normalize_rejects_what_the_grammar_rejects() -> None:
    assert normalize_expr("$clog2(D)") is None
    assert normalize_expr("A/B") is None
    assert normalize_expr(None) is None


# --- substitution -------------------------------------------------------------


def test_substitute_replaces_only_known_names() -> None:
    assert substitute_params("WIDTH-1", {"WIDTH": "19"}) == "19-1"
    assert substitute_params("WIDTH-1", {"OTHER": "19"}) == "WIDTH-1"
    assert substitute_params("WIDTH-1", None) == "WIDTH-1"
    assert substitute_params("WIDTH-1", {}) == "WIDTH-1"
    # An empty value is not a substitution — it would delete the name.
    assert substitute_params("WIDTH-1", {"WIDTH": "  "}) == "WIDTH-1"


def test_substitute_parenthesises_a_compound_value() -> None:
    """``.W(A+B)`` in ``W*2`` is ``(A+B)*2``, not ``A+B*2``."""
    assert substitute_params("W*2", {"W": "A+B"}) == "(A+B)*2"
    assert substitute_params("W*2", {"W": "PTR_W"}) == "PTR_W*2"
    assert substitute_params("W*2", {"W": "8"}) == "8*2"


def test_substitute_does_not_chase_a_value_s_own_identifiers() -> None:
    """One pass: ``W -> PTR_W`` stops there even if ``PTR_W`` is known."""
    params = {"W": "PTR_W", "PTR_W": "4"}
    assert substitute_params("W-1", params) == "PTR_W-1"


def test_substitute_skips_field_selects_and_system_functions() -> None:
    assert substitute_params("$clog2(W)", {"clog2": "9"}) == "$clog2(W)"
    assert substitute_params("cfg.W", {"W": "9"}) == "cfg.W"


# --- ranges -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("type_text", "expected"),
    [
        ("logic [7:0]", ("7", "0")),
        ("logic [PTR_W-1:0]", ("PTR_W-1", "0")),
        ("logic  [ W - 1 : 0 ]", ("W - 1", "0")),
        ("logic [SIZES[0]-1:0]", ("SIZES[0]-1", "0")),
        ("logic", None),
        ("logic [8]", None),  # a size, not a range
        ("logic [7:0", None),  # unbalanced
        ("logic [a:b:c]", None),
        (None, None),
    ],
)
def test_leading_range_cuts_at_the_top_level_colon(
    type_text: str | None, expected: tuple[str, str] | None
) -> None:
    assert leading_range(type_text) == expected


def test_leading_range_reads_the_first_dimension_only() -> None:
    assert leading_range("logic [3:0][7:0]") == ("3", "0")


# --- simplification -----------------------------------------------------------


@pytest.mark.parametrize(
    ("msb", "lsb", "expected"),
    [
        ("PTR_W-1", "0", "PTR_W"),
        ("PTR_W - 1", "0", "PTR_W"),
        ("W+K-1", "0", "W+K"),
        ("(A+B)-1", "0", "(A+B)"),
        ("W*2-1", "0", "W*2"),
        # Not clean: nothing to print.
        ("PTR_W", "0", None),  # no ``-1`` tail
        ("PTR_W-2", "0", None),
        ("PTR_W-1", "1", None),  # lsb isn't 0
        ("PTR_W-1", "LSB", None),
        ("$clog2(D)-1", "0", None),
        ("W/2-1", "0", None),
        ("8-1", "0", None),  # numeric — tier 1 owns it
        ("-1", "0", None),
        ("", "0", None),
    ],
)
def test_simplify_range_matches_one_clean_pattern(
    msb: str, lsb: str, expected: str | None
) -> None:
    assert simplify_range(msb, lsb) == expected


def test_simplify_range_refuses_an_expression_too_long_to_print() -> None:
    long_name = "A" * (MAX_EXPR_LEN + 1)
    assert simplify_range(f"{long_name}-1", "0") is None
    short = "A" * MAX_EXPR_LEN
    assert simplify_range(f"{short}-1", "0") == short


# --- the two width answers ----------------------------------------------------


def test_width_of_folds_a_substituted_literal() -> None:
    assert width_of("logic [WIDTH-1:0]", {"WIDTH": "19"}) == 19
    assert width_of("logic [DEPTH*2-1:0]", {"DEPTH": "4"}) == 8
    assert width_of("logic [WIDTH-1:0]", {"WIDTH": "PTR_W"}) is None
    assert width_of("logic [$clog2(D)-1:0]", {"D": "8"}) is None
    assert width_of("logic", {"WIDTH": "19"}) is None


def test_width_expr_reads_the_declaration_not_the_binding_site() -> None:
    """The name is the intent; the folded number is a detail.

    ``width_expr_of`` takes no parameters *by design* — substituting
    would replace ``WIDTH`` with the caller's own name, or with one
    instantiation's integer, and lose what the designer wrote.
    """
    assert width_expr_of("logic [WIDTH-1:0]") == "WIDTH"
    assert width_expr_of("logic [PTR_W-1:0]") == "PTR_W"
    assert width_expr_of("logic") is None
    assert width_expr_of(None) is None


def test_a_numerically_written_range_has_no_expression() -> None:
    """A number is not algebra — ``bits`` already says 19."""
    assert width_expr_of("logic [18:0]") is None
    assert width_expr_of("logic [8-1:0]") is None


def test_both_answers_coexist_for_one_declaration() -> None:
    """``[WIDTH-1:0]`` + ``.WIDTH(19)`` is 19 bits *and* ``WIDTH``-wide."""
    assert width_of("logic [WIDTH-1:0]", {"WIDTH": "19"}) == 19
    assert width_expr_of("logic [WIDTH-1:0]") == "WIDTH"


# --- bundle summing -----------------------------------------------------------


def test_sum_one_symbolic_term_plus_the_numeric_remainder() -> None:
    assert sum_width_exprs(["PTR_W"], 0) == "PTR_W"
    assert sum_width_exprs(["PTR_W"], 1) == "PTR_W+1"
    assert sum_width_exprs(["PTR_W"], 12) == "PTR_W+12"


def test_sum_abstains_without_exactly_one_symbolic_term() -> None:
    """Two terms — identical ones included — is where we stop.

    Folding ``PTR_W + PTR_W`` into ``2*PTR_W`` needs an equality
    between two source texts that a structural tool cannot justify.
    """
    assert sum_width_exprs([], 8) is None
    assert sum_width_exprs(["PTR_W", "DATA_W"], 0) is None
    assert sum_width_exprs(["PTR_W", "PTR_W"], 0) is None


def test_sum_abstains_when_the_result_would_not_fit_on_a_wire() -> None:
    assert sum_width_exprs(["A" * MAX_EXPR_LEN], 1) is None
    assert sum_width_exprs(["A" * MAX_EXPR_LEN], 0) == "A" * MAX_EXPR_LEN


def test_sum_of_a_negative_remainder_stays_readable() -> None:
    """Not reachable from real widths, but the string must still parse."""
    assert sum_width_exprs(["PTR_W"], -2) == "PTR_W-2"
