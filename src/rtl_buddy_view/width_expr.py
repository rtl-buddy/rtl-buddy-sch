"""Width resolution beyond the integer-literal range (epic #163).

``logic [7:0]`` is 8 bits and every layer of this tool already knows
it. ``logic [PTR_W-1:0]`` is the interesting case: pre-elaboration
there is no number to print, and the shipped behaviour — abstain, draw
no slash — silently loses the one thing the reader wanted, which is
*how wide the bus is*. This module computes the two answers a
structural analyzer can honestly give. They are **independent**, and
a declaration may yield both:

**The number — parameter substitution.** A range whose bounds
reference parameters is re-read with those parameters textually
substituted (``[WIDTH-1:0]`` + ``#(.WIDTH(19))`` → ``[19-1:0]``) and
the arithmetic folded by :func:`eval_int_expr`. When it folds, the
answer is a real number and nothing downstream can tell it apart from
a literal range. Strictly additive: substitution only runs where the
literal parse already failed, so no width that resolves today can
change.

**The expression — read off the declaration, unsubstituted.**
:func:`simplify_range` recognises one clean pattern — ``[X-1:0]`` →
``X`` — over the range **as written**. ``[WIDTH-1:0]`` is ``"WIDTH"``
whether or not an override folds it to 19, because the *parameter
name is the design intent* and the folded number is an elaboration
detail: a reader who sees ``/WIDTH`` learns which knob moves that bus,
which ``/19`` cannot tell them. A range already written in integers
(``[18:0]``) has no expression at all — a number is not algebra.
Anything that does not match the clean pattern returns ``None``:
silence beats noise on a drawing.

Three deliberate limits:

* **No ``eval``, no ``ast``.** :func:`eval_int_expr` is a hand-rolled
  tokenizer plus a recursive-descent parser over integers, ``+``,
  ``-``, ``*``, parentheses and unary minus. Anything else — a
  ``$clog2`` call, ``/``, ``**``, a ternary, a sized literal, an
  unresolved identifier — is a parse abstention, not an approximation.
  Input length and recursion depth are capped so a pathological string
  cannot cost more than it is worth.
* **One substitution pass.** A parameter whose value references
  another parameter is not chased. ``$clog2(DEPTH)`` would abstain
  anyway, and a fixed-point iteration over unelaborated text is how a
  structural tool starts inventing numbers.
* **This is a stopgap for tier 1.** A real elaborator (the slang
  frontend, ``frontend/slang.py``) resolves parameters properly and
  supersedes the substitution tier entirely. Tier 2 survives it only
  for the genuinely unelaborated case.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

#: Longest expression string worth printing on a wire. A width
#: annotation competes for space with the net label; past ~20
#: characters it stops being an annotation and starts being a second
#: label, so the caller abstains instead.
MAX_EXPR_LEN = 20

#: Refuse to even tokenize beyond this. A width bound is a handful of
#: tokens; anything longer is a macro body or a pasted file, and
#: parsing it can only end in an abstention.
_MAX_INPUT_LEN = 200

#: Parenthesis / unary-minus nesting cap. Python's own recursion limit
#: would raise on ``"(" * 5000``, and a RecursionError escaping a pure
#: analyzer function is a crash, not an abstention.
_MAX_DEPTH = 32

#: An identifier token that is *not* a field select (not preceded by
#: ``.``), not the base part of a sized literal (not preceded by
#: ``'``), and not a system task/function name (not preceded by
#: ``$``). The lookbehind also excludes identifier characters so the
#: tail of a longer identifier never matches on its own.
#:
#: Shared with :mod:`rtl_buddy_view.connectivity`, which imports it as
#: its root-identifier pattern: "the leading name of a reference
#: chain" and "a name a parameter value may be substituted for" are
#: the same lexical question, and two copies would drift.
IDENT_RE = re.compile(r"(?<![A-Za-z0-9_$.'])[A-Za-z_][A-Za-z0-9_$]*")

#: A substituted value that needs no parentheses: a bare identifier or
#: a bare decimal integer binds tighter than any operator around it.
_ATOM_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*|\d+")

#: One token of the tiny expression grammar: a decimal integer, an
#: identifier, or one of ``+ - * ( )``. Every other character —
#: ``$``, ``'``, ``/``, ``?``, ``:``, ``{`` — ends the tokenization,
#: which is how ``$clog2(N)`` and ``4'd8`` abstain.
_TOKEN_RE = re.compile(r"\s*(\d+|[A-Za-z_][A-Za-z0-9_$]*|[()+\-*])")

#: ``<msb> - 1`` — the one clean algebraic shape (see
#: :func:`simplify_range`).
_MINUS_ONE_RE = re.compile(r"^(.+?)\s*-\s*1$", re.DOTALL)


class _Abstain(Exception):
    """Internal: the parser met something it will not guess at."""


# --- the safe evaluator ------------------------------------------------------


def _tokenize(text: str) -> list[str] | None:
    """Split ``text`` into grammar tokens, or ``None`` if it can't be.

    Rejects the whole string on the first unrecognised character
    rather than skipping it: a partially-understood expression is
    exactly the thing that produces a confidently wrong number.
    """
    stripped = text.strip()
    if not stripped or len(stripped) > _MAX_INPUT_LEN:
        return None
    tokens: list[str] = []
    pos = 0
    while pos < len(stripped):
        match = _TOKEN_RE.match(stripped, pos)
        if match is None:
            return None
        tokens.append(match.group(1))
        pos = match.end()
    return tokens


class _Parser:
    """Recursive descent over ``expr := term (('+'|'-') term)*``.

    ``term := factor ('*' factor)*`` and
    ``factor := '-' factor | '(' expr ')' | INT | IDENT``.

    The value channel carries ``None`` for "syntactically fine but not
    a number" — an identifier, or any arithmetic involving one. That
    is what lets the same parser answer both questions this module
    asks: *does it fold?* (a non-``None`` value) and *is it clean?*
    (it parsed at all).
    """

    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens
        self._pos = 0

    def peek(self) -> str | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _take(self) -> str:
        token = self.peek()
        if token is None:
            raise _Abstain
        self._pos += 1
        return token

    def expr(self, depth: int) -> int | None:
        if depth > _MAX_DEPTH:
            raise _Abstain
        value = self.term(depth)
        while self.peek() in ("+", "-"):
            op = self._take()
            rhs = self.term(depth)
            if value is None or rhs is None:
                value = None
            else:
                value = value + rhs if op == "+" else value - rhs
        return value

    def term(self, depth: int) -> int | None:
        value = self.factor(depth)
        while self.peek() == "*":
            self._take()
            rhs = self.factor(depth)
            value = None if value is None or rhs is None else value * rhs
        return value

    def factor(self, depth: int) -> int | None:
        if depth > _MAX_DEPTH:
            raise _Abstain
        token = self._take()
        if token == "-":
            value = self.factor(depth + 1)
            return None if value is None else -value
        if token == "(":
            value = self.expr(depth + 1)
            if self._take() != ")":
                raise _Abstain
            return value
        if token.isdigit():
            return int(token)
        if _ATOM_RE.fullmatch(token):
            return None  # an identifier: valid, but not a number
        raise _Abstain


def _parse(text: str | None) -> tuple[bool, int | None]:
    """``(parsed cleanly?, folded value or None)``."""
    if text is None:
        return False, None
    tokens = _tokenize(text)
    if tokens is None:
        return False, None
    parser = _Parser(tokens)
    try:
        value = parser.expr(0)
    except _Abstain:
        return False, None
    if parser.peek() is not None:
        # Trailing tokens — two adjacent atoms (``2 3``), an unclosed
        # paren's tail, a stray operator. Never "close enough".
        return False, None
    return True, value


def eval_int_expr(text: str | None) -> int | None:
    """Fold an integer expression, or ``None`` if it doesn't fold.

    Handles decimal integers, ``+``, ``-``, ``*``, parentheses and
    unary minus — nothing else::

        eval_int_expr("19-1")      == 18
        eval_int_expr("2*(3+1)-1") == 7
        eval_int_expr("$clog2(8)") is None
        eval_int_expr("8/2")       is None
        eval_int_expr("W-1")       is None

    No ``eval``, no ``ast``: the input is unelaborated SystemVerilog
    source text, and the only safe way to read attacker-shaped text is
    a grammar that cannot express anything but arithmetic.
    """
    return _parse(text)[1]


def normalize_expr(text: str | None) -> str | None:
    """Whitespace-normalized form of a clean expression, else ``None``.

    ``"A + B"`` → ``"A+B"``. Joining tokens (rather than deleting
    whitespace) is what makes this safe: ``"2 3"`` does not become
    ``"23"``, it fails to parse.
    """
    if text is None:
        return None
    tokens = _tokenize(text)
    if tokens is None or not _parse(text)[0]:
        return None
    return "".join(tokens)


# --- parameter substitution ---------------------------------------------------


def substitute_params(text: str, params: Mapping[str, str] | None) -> str:
    """Replace parameter identifiers in ``text`` with their value text.

    One pass, verbatim values, no recursion into a value's own
    identifiers. A value that is not a single atom is parenthesised,
    so ``.W(A+B)`` in ``[W*2-1:0]`` becomes ``[(A+B)*2-1:0]`` rather
    than a precedence bug.

    Names not in ``params`` are left alone — that is the whole point
    of the tier-2 path, which reads what is left standing.
    """
    if not params:
        return text

    def replace(match: re.Match[str]) -> str:
        value = params.get(match.group(0))
        if value is None:
            return match.group(0)
        stripped = value.strip()
        if not stripped:
            return match.group(0)
        return stripped if _ATOM_RE.fullmatch(stripped) else f"({stripped})"

    return IDENT_RE.sub(replace, text)


# --- ranges -------------------------------------------------------------------


def leading_range(type_text: str | None) -> tuple[str, str] | None:
    """``(msb, lsb)`` source text of the leading packed range.

    ``"logic [PTR_W-1:0]"`` → ``("PTR_W-1", "0")``. Bracket-depth
    aware, so an indexed bound (``[SIZES[0]-1:0]``) is cut at the
    right colon; ``None`` when there is no leading ``[…:…]`` at all —
    an unpacked size (``[8]``), an unbalanced bracket, or a nested
    colon that makes the split ambiguous.
    """
    if not type_text:
        return None
    start = type_text.find("[")
    if start < 0:
        return None
    depth = 0
    end = -1
    for index in range(start, len(type_text)):
        char = type_text[index]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                end = index
                break
    if end < 0:
        return None
    inner = type_text[start + 1 : end]
    depth = 0
    cut = -1
    for index, char in enumerate(inner):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == ":" and depth == 0:
            if cut >= 0:
                return None  # ``[a:b:c]`` — not a packed range we know
            cut = index
    if cut < 0:
        return None
    return inner[:cut].strip(), inner[cut + 1 :].strip()


def range_width(msb: str, lsb: str) -> int | None:
    """Bit count of a range whose bounds both fold, else ``None``."""
    high = eval_int_expr(msb)
    low = eval_int_expr(lsb)
    if high is None or low is None:
        return None
    return abs(high - low) + 1


def simplify_range(msb: str, lsb: str) -> str | None:
    """The algebraic width of a range, or ``None`` when it isn't clean.

    **The whole rule set**, deliberately tiny and deliberately
    deterministic (no CAS, no rewriting):

    * ``lsb`` must fold to literal ``0``. A range that does not start
      at bit 0 has an offset the reader needs to see, and hiding it
      inside a single symbol would be a lie.
    * ``msb`` must end in ``- 1``. The result is the rest of ``msb``,
      whitespace-normalized: ``[X-1:0]`` → ``X``, ``[X+K-1:0]`` →
      ``X+K``.
    * What is left must parse as the clean arithmetic grammar
      (identifiers, integers, ``+ - *``, parentheses) and must still
      **contain an identifier**. ``[18:0]`` and ``[8-1:0]`` therefore
      have no expression: a number is not algebra, and writing one out
      as text would be a second copy of ``bits`` in a field that
      promises a name.
    * It must fit in :data:`MAX_EXPR_LEN` characters.

    Everything else — ``[X:0]``, ``[X-2:0]``, ``[$clog2(D)-1:0]``,
    ``[X-1:1]`` — returns ``None``. Silence beats noise: an unlabelled
    wire is read as "unknown", a wrong or cryptic label is read as
    fact.
    """
    if eval_int_expr(lsb) != 0:
        return None
    match = _MINUS_ONE_RE.match(msb.strip())
    if match is None:
        return None
    head = normalize_expr(match.group(1))
    if head is None or not head:
        return None
    if eval_int_expr(head) is not None:
        return None  # numeric — not algebra
    if len(head) > MAX_EXPR_LEN:
        return None
    return head


# --- the two public width answers ---------------------------------------------


def width_of(
    type_text: str | None, params: Mapping[str, str] | None = None
) -> int | None:
    """Numeric width of a declared type after parameter substitution.

    ``None`` when the bounds do not fold to integers — which is every
    case the literal parse in
    :func:`rtl_buddy_view.connectivity.port_width` already handles, so
    this is only ever consulted as its fallback.
    """
    bounds = leading_range(type_text)
    if bounds is None:
        return None
    msb, lsb = (substitute_params(text, params) for text in bounds)
    return range_width(msb, lsb)


def width_expr_of(type_text: str | None) -> str | None:
    """Algebraic width of a declared type, or ``None``.

    Read off the range **as written**, with no parameter substitution
    — and deliberately *not* subordinate to :func:`width_of`::

        width_expr_of("logic [WIDTH-1:0]")    == "WIDTH"   # even when
                                                           # .WIDTH(19)
                                                           # folds to 19
        width_expr_of("logic [PTR_W-1:0]")    == "PTR_W"
        width_expr_of("logic [18:0]")         is None
        width_expr_of("logic")                is None

    Two decisions live in that first line.

    **No substitution.** The parameter *name* is what the designer
    wrote and what carries the intent: ``/WIDTH`` tells a reader which
    knob moves this bus, which ``/PTR_W`` (the caller's local name) or
    ``/19`` (an elaboration detail of one instantiation) cannot. The
    number is still computed — by :func:`width_of`, from the
    substituted bounds — and both travel together.

    **Both answers coexist.** A pin may be 19 bits *and* be
    ``WIDTH``-wide; nothing here picks between them. Display does
    (elk.json § 6.2): the canvas prefers the expression, whose
    compactness the abstention rules already guarantee.

    A range written in integers has no expression at all — see
    :func:`simplify_range`.
    """
    bounds = leading_range(type_text)
    if bounds is None:
        return None
    return simplify_range(*bounds)


def sum_width_exprs(terms: list[str], numeric: int) -> str | None:
    """Combine a bundle's symbolic terms with its numeric remainder.

    A bundle is a sum of net widths, and a sum of one symbol and a
    pile of integers is still legible: ``["PTR_W"], 1`` → ``"PTR_W+1"``
    (the symbol first, then the folded remainder; ``+0`` is omitted).

    **Two or more symbolic terms abstain**, identical ones included.
    Folding ``PTR_W + PTR_W`` into ``2*PTR_W`` is arithmetic this
    module deliberately does not do: the moment it starts collecting
    like terms it needs an algebra, and an algebra needs a notion of
    equality between two source texts that a structural tool does not
    have (``PTR_W`` from two scopes may be two different localparams).
    Counting them as two terms and staying quiet costs one annotation
    and buys the rule "every expression printed is one net's declared
    width, verbatim".

    ``None`` also when there is no symbolic term at all (``bits``
    covers that) or when the result outgrows :data:`MAX_EXPR_LEN`.
    """
    if len(terms) != 1:
        return None
    if numeric == 0:
        out = terms[0]
    elif numeric > 0:
        out = f"{terms[0]}+{numeric}"
    else:
        out = f"{terms[0]}-{abs(numeric)}"
    return out if len(out) <= MAX_EXPR_LEN else None
