"""Generate-construct unrolling (rtl-buddy-sch#209).

The Verible frontend reads source, not an elaborated design, so a
``for (genvar …) begin : g … end`` loop or an ``if (PARAM) begin : g``
block reaches it as a *template*
(:attr:`rtl_buddy_view.extractor.Module.generate_body`). This module
turns that template into the flat instance and assign lists every
downstream layer already understands, naming each instance the way
slang and simulators do:

* a loop iteration is ``<label>[<i>].<inst>`` — ``g[0].u_s``;
* a conditional arm is ``<label>.<inst>`` — ``g.u_x``;
* an unnamed block takes the LRM default ``genblk<N>`` (IEEE
  1800-2017 § 27.6), numbered by the construct's position in its
  scope. The frontend assigns those labels; this module only reads
  them.

Port bindings, parameter overrides and assigns inside an iteration
get that iteration's genvar value substituted (``bq[t].v`` →
``bq[0].v``), and a net or instance declared inside a generate block
is qualified with its scope (``w`` → ``g[0].w``; an interface array
``bus_if sub [0:1] ();`` bound as ``sub[1]`` → ``g[0].sub[1]``,
rtl-buddy-sch#218), so two iterations never share a net or an
interface they don't share in hardware.

**When the evaluator cannot decide**, nothing is dropped:

* a loop whose bounds don't fold becomes one *symbolic* iteration,
  ``g[*].u_s``, with the genvar written as ``*`` in its bindings;
* an ``if`` / ``case`` whose condition doesn't fold keeps every arm
  that is not ruled out, and marks their instances with
  :attr:`~rtl_buddy_view.extractor.Instance.generate_condition` (the
  undecided guards, so a reader can see what the arm depends on).

The evaluator (:func:`eval_const`) is a sibling of
:func:`rtl_buddy_view.width_expr.eval_int_expr`, not a reuse of it:
that one deliberately abstains on anything past ``+ - *`` because a
width it cannot fold is better unlabelled, whereas a loop bound needs
comparisons, ``/``, shifts, ``$clog2`` and parameter chains
(``localparam N = 2*W``). Same rules otherwise — no ``eval``, no
``ast``, capped input, depth and iteration counts, and abstention
(``None``) instead of a guess.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping

from rtl_buddy_view.extractor import (
    Assign,
    GenerateConditional,
    GenerateItem,
    GenerateLoop,
    Instance,
    Module,
    Parameter,
    ParameterOverride,
    PortConnection,
)
from rtl_buddy_view.width_expr import IDENT_RE

#: The index a symbolic (unresolvable-bounds) loop iteration carries.
SYMBOLIC_INDEX = "*"

#: Most iterations one loop may unroll to before it is drawn as a
#: single symbolic iteration instead. Past this a figure is unreadable
#: anyway, and a runaway step (``i = i``) must terminate.
MAX_ITERATIONS = 1024

#: Name → value text; ``None`` marks a name that exists but whose value
#: is unknown (an override the parent could not fold).
Env = Mapping[str, str | None]

_MAX_INPUT_LEN = 400
_MAX_DEPTH = 48

_TOKEN_RE = re.compile(
    r"""\s*(?:
        (?P<based>(?:\d[\d_]*)?\s*'[sS]?[bBoOdDhH]\s*[0-9a-fA-F_xXzZ?]+)
      | (?P<num>\d[\d_]*)
      | (?P<str>"(?:\\.|[^"\\])*")
      | (?P<sys>\$[A-Za-z_][A-Za-z0-9_$]*)
      | (?P<ident>[A-Za-z_][A-Za-z0-9_$]*)
      | (?P<op><<<|>>>|===|!==|\*\*|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%<>!~&|^?:(),])
    )""",
    re.VERBOSE,
)

_BASES = {"b": 2, "o": 8, "d": 10, "h": 16}

#: Binary operators by precedence, loosest first (IEEE 1800 table 11-2;
#: ``?:`` is handled above this table).
_BINARY_LEVELS: tuple[tuple[str, ...], ...] = (
    ("||",),
    ("&&",),
    ("|",),
    ("^",),
    ("&",),
    ("==", "!=", "===", "!=="),
    ("<", "<=", ">", ">="),
    ("<<", ">>", "<<<", ">>>"),
    ("+", "-"),
    ("*", "/", "%"),
    ("**",),
)

_Value = int | str


class _Abstain(Exception):
    """Internal: the expression is outside what we will evaluate."""


def _tokenize(text: str) -> list[tuple[str, str]]:
    stripped = text.strip()
    if not stripped or len(stripped) > _MAX_INPUT_LEN:
        raise _Abstain
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(stripped):
        match = _TOKEN_RE.match(stripped, pos)
        if match is None or match.end() == pos:
            raise _Abstain
        kind = match.lastgroup
        assert kind is not None
        tokens.append((kind, match.group(kind)))
        pos = match.end()
    return tokens


def _based_value(text: str) -> int:
    _, _, rest = text.partition("'")
    rest = rest.strip()
    if rest[:1] in "sS":
        rest = rest[1:]
    base = _BASES[rest[0].lower()]
    digits = rest[1:].strip().replace("_", "")
    if any(c in "xXzZ?" for c in digits):
        raise _Abstain  # four-state literal: not a number
    try:
        return int(digits, base)
    except ValueError as exc:
        raise _Abstain from exc


class _Parser:
    def __init__(
        self, tokens: list[tuple[str, str]], env: Env, depth: int, seen: frozenset
    ) -> None:
        self._tokens = tokens
        self._pos = 0
        self._env = env
        self._depth = depth
        self._seen = seen

    def _peek(self) -> str | None:
        if self._pos >= len(self._tokens):
            return None
        kind, text = self._tokens[self._pos]
        return text if kind == "op" else None

    def _take(self) -> tuple[str, str]:
        if self._pos >= len(self._tokens):
            raise _Abstain
        token = self._tokens[self._pos]
        self._pos += 1
        return token

    def _expect(self, op: str) -> None:
        if self._take() != ("op", op):
            raise _Abstain

    def done(self) -> bool:
        return self._pos == len(self._tokens)

    def expr(self, depth: int = 0) -> _Value:
        if depth > _MAX_DEPTH:
            raise _Abstain
        cond = self._binary(0, depth)
        if self._peek() != "?":
            return cond
        self._take()
        then = self.expr(depth + 1)
        self._expect(":")
        other = self.expr(depth + 1)
        return then if _truth(cond) else other

    def _binary(self, level: int, depth: int) -> _Value:
        if level == len(_BINARY_LEVELS):
            return self._unary(depth)
        ops = _BINARY_LEVELS[level]
        lhs = self._binary(level + 1, depth)
        while self._peek() in ops:
            op = self._take()[1]
            rhs = self._binary(level + 1, depth)
            lhs = _apply(op, lhs, rhs)
        return lhs

    def _unary(self, depth: int) -> _Value:
        if depth > _MAX_DEPTH:
            raise _Abstain
        op = self._peek()
        if op in ("-", "+", "!", "~"):
            self._take()
            value = _as_int(self._unary(depth + 1))
            if op == "-":
                return -value
            if op == "+":
                return value
            if op == "!":
                return int(not value)
            return ~value
        return self._primary(depth)

    def _primary(self, depth: int) -> _Value:
        kind, text = self._take()
        if kind == "num":
            return int(text.replace("_", ""))
        if kind == "based":
            return _based_value(text)
        if kind == "str":
            return text[1:-1]
        if kind == "ident":
            return self._lookup(text)
        if kind == "sys":
            if text != "$clog2":
                raise _Abstain
            self._expect("(")
            arg = _as_int(self.expr(depth + 1))
            self._expect(")")
            return 0 if arg <= 1 else (arg - 1).bit_length()
        if (kind, text) == ("op", "("):
            value = self.expr(depth + 1)
            self._expect(")")
            return value
        raise _Abstain

    def _lookup(self, name: str) -> _Value:
        if name in self._seen or name not in self._env:
            raise _Abstain
        text = self._env[name]
        if text is None:
            raise _Abstain
        return _evaluate(text, self._env, self._depth + 1, self._seen | {name})


def _as_int(value: _Value) -> int:
    if isinstance(value, str):
        raise _Abstain
    return value


def _truth(value: _Value) -> bool:
    return _as_int(value) != 0


def _apply(op: str, lhs: _Value, rhs: _Value) -> _Value:
    if op in ("==", "!=", "===", "!=="):
        if isinstance(lhs, str) != isinstance(rhs, str):
            raise _Abstain
        equal = lhs == rhs
        return int(equal if op in ("==", "===") else not equal)
    a, b = _as_int(lhs), _as_int(rhs)
    if op == "||":
        return int(bool(a) or bool(b))
    if op == "&&":
        return int(bool(a) and bool(b))
    if op in ("/", "%"):
        if b == 0:
            raise _Abstain
        quotient = abs(a) // abs(b) * (1 if (a >= 0) == (b >= 0) else -1)
        return quotient if op == "/" else a - b * quotient
    if op in ("<<", "<<<", ">>", ">>>", "**") and (b < 0 or b > 64):
        raise _Abstain
    simple = {
        "|": lambda: a | b,
        "^": lambda: a ^ b,
        "&": lambda: a & b,
        "<": lambda: int(a < b),
        "<=": lambda: int(a <= b),
        ">": lambda: int(a > b),
        ">=": lambda: int(a >= b),
        "<<": lambda: a << b,
        "<<<": lambda: a << b,
        ">>": lambda: a >> b,
        ">>>": lambda: a >> b,
        "+": lambda: a + b,
        "-": lambda: a - b,
        "*": lambda: a * b,
        "**": lambda: a**b,
    }
    return simple[op]()


def _evaluate(text: str, env: Env, depth: int, seen: frozenset) -> _Value:
    if depth > _MAX_DEPTH:
        raise _Abstain
    parser = _Parser(_tokenize(text), env, depth, seen)
    value = parser.expr()
    if not parser.done():
        raise _Abstain
    return value


def eval_value(text: str | None, env: Env) -> int | str | None:
    """Fold a constant expression to an ``int`` or a string literal.

    Identifiers resolve through ``env`` (recursively, so
    ``localparam N = 2*W`` works); anything else — an unknown name, an
    unsupported system call, a four-state literal — is ``None``.
    """
    if text is None:
        return None
    try:
        return _evaluate(text, env, 0, frozenset())
    except (_Abstain, RecursionError):
        return None


def eval_const(text: str | None, env: Env) -> int | None:
    """:func:`eval_value`, integers only."""
    value = eval_value(text, env)
    return value if isinstance(value, int) else None


def eval_bool(text: str | None, env: Env) -> bool | None:
    """A generate condition's truth value, or ``None`` if undecidable."""
    value = eval_const(text, env)
    return None if value is None else value != 0


# --- parameter environments --------------------------------------------------


def _literal_text(value: int | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        return str(value)
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def module_env(
    module: Module,
    overrides: tuple[ParameterOverride, ...] = (),
    outer_env: Env | None = None,
) -> dict[str, str | None]:
    """The parameter environment one placement of ``module`` sees.

    Header defaults, then the placement's overrides — each folded in
    the *parent's* environment ``outer_env`` first, since an override
    is written in the parent's names (``.N(LANES)``); one that does
    not fold is recorded as unknown rather than re-read in the child's
    scope — then the body's own ``localparam``s. A positional override
    binds by declared order, the same rule
    :func:`rtl_buddy_view.connectivity.instance_params` uses.
    """
    env: dict[str, str | None] = {
        p.name: p.default_text for p in module.parameters if p.default_text
    }
    declared = [p.name for p in module.parameters]
    for index, override in enumerate(overrides):
        name = override.param_name
        if name is None:
            if index >= len(declared):
                continue
            name = declared[index]
        if outer_env is None:
            env[name] = override.value_text
        else:
            env[name] = _literal_text(eval_value(override.value_text, outer_env))
    for lp in module.local_params:
        if lp.default_text:
            env[lp.name] = lp.default_text
    return env


# --- unrolling ----------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _Scope:
    """Where an item is being unrolled into."""

    prefix: str  # instance-name prefix, e.g. "g[0]."
    env: Env
    #: genvar → substituted text (its value, or ``*`` when symbolic)
    genvars: Mapping[str, str]
    #: local net → qualified name (``w`` → ``g[0].w``)
    nets: Mapping[str, str]
    #: undecided guards, already rendered (``"USE"``, ``"!(A)"``)
    guards: tuple[str, ...]


_BRACKET_RE = re.compile(r"\[([^\[\]]*)\]")
_SELECT_SPLIT_RE = re.compile(r"(\+:|-:|:)")


def _substitute(text: str, scope: _Scope) -> str:
    """Write genvar values and qualified local nets into ``text``."""
    if not text or (not scope.genvars and not scope.nets):
        return text
    hit = False

    def replace(match: re.Match[str]) -> str:
        nonlocal hit
        name = match.group(0)
        if name in scope.genvars:
            hit = True
            return scope.genvars[name]
        if name in scope.nets:
            return scope.nets[name]
        return name

    out = IDENT_RE.sub(replace, text)
    if not hit:
        return out
    return _BRACKET_RE.sub(_fold_select, out)


def _fold_select(match: re.Match[str]) -> str:
    """``[0*8 +: 8]`` → ``[0 +: 8]``: fold each bound that is constant."""
    parts = _SELECT_SPLIT_RE.split(match.group(1))
    folded: list[str] = []
    for part in parts:
        if _SELECT_SPLIT_RE.fullmatch(part):
            folded.append(part)
            continue
        value = eval_const(part, {}) if part.strip() else None
        if value is None or part.strip().isdigit():
            folded.append(part)
        else:
            lead = part[: len(part) - len(part.lstrip())]
            trail = part[len(part.rstrip()) :]
            folded.append(f"{lead}{value}{trail}")
    return "[" + "".join(folded) + "]"


def _unroll_instance(inst: Instance, scope: _Scope) -> Instance:
    if not scope.prefix and not scope.genvars and not scope.nets and not scope.guards:
        return inst
    return dataclasses.replace(
        inst,
        name=scope.prefix + inst.name,
        param_overrides=tuple(
            dataclasses.replace(ov, value_text=_substitute(ov.value_text, scope))
            for ov in inst.param_overrides
        ),
        port_connections=tuple(
            _unroll_connection(conn, scope) for conn in inst.port_connections
        ),
        generate_condition=" && ".join(scope.guards) if scope.guards else None,
    )


def _unroll_connection(conn: PortConnection, scope: _Scope) -> PortConnection:
    text = conn.net_expr_text
    if not text.strip() and conn.port_name is not None:
        # ``.name`` shorthand binds the same-named net, which may be a
        # generate-local one; spell it out so it can be qualified.
        if conn.port_name in scope.nets:
            return dataclasses.replace(conn, net_expr_text=scope.nets[conn.port_name])
        return conn
    return dataclasses.replace(conn, net_expr_text=_substitute(text, scope))


def _with_locals(
    scope: _Scope,
    prefix: str,
    local_nets: tuple[str, ...],
    local_params: tuple[Parameter, ...],
    genvars: Mapping[str, str],
    env: Env,
    guards: tuple[str, ...],
    items: tuple[GenerateItem, ...] = (),
) -> _Scope:
    nets = dict(scope.nets)
    for name in local_nets:
        nets[name] = prefix + name
    # An instance declared in the block is scoped to it just like a
    # net (rtl-buddy-sch#218): ``bus_if sub [0:1] ();`` inside ``g``
    # is ``g[0].sub`` in iteration 0, and a sibling's ``.src(sub[1])``
    # must bind that copy — not a module-scope ``sub`` every iteration
    # would share.
    for item in items:
        if isinstance(item, Instance):
            nets[item.name] = prefix + item.name
    inner_env = dict(env)
    for lp in local_params:
        value = eval_value(lp.default_text, inner_env)
        inner_env[lp.name] = (
            _literal_text(value) if value is not None else lp.default_text
        )
    return _Scope(
        prefix=prefix, env=inner_env, genvars=genvars, nets=nets, guards=guards
    )


_INIT_RE = re.compile(r"^\s*(?:genvar\s+)?([A-Za-z_][A-Za-z0-9_$]*)\s*=\s*(.+?)\s*$")
_INCDEC_RE = re.compile(
    r"^\s*(?:(\+\+|--)\s*([A-Za-z_][A-Za-z0-9_$]*)|([A-Za-z_][A-Za-z0-9_$]*)\s*(\+\+|--))\s*$"
)
_COMPOUND_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_$]*)\s*(\+|-|\*|/|%|<<|>>)?=\s*(.+?)\s*$"
)


def _next_value(step_text: str, genvar: str, value: int, env: Env) -> int | None:
    incdec = _INCDEC_RE.match(step_text)
    if incdec is not None:
        name = incdec.group(2) or incdec.group(3)
        op = incdec.group(1) or incdec.group(4)
        if name != genvar:
            return None
        return value + 1 if op == "++" else value - 1
    compound = _COMPOUND_RE.match(step_text)
    if compound is None or compound.group(1) != genvar:
        return None
    rhs_env = {**env, genvar: str(value)}
    op = compound.group(2)
    rhs = compound.group(3)
    expr = rhs if op is None else f"{genvar} {op} ({rhs})"
    return eval_const(expr, rhs_env)


def loop_indices(loop: GenerateLoop, env: Env) -> tuple[int, ...] | None:
    """The genvar values ``loop`` iterates over, or ``None`` if unknown."""
    init = _INIT_RE.match(loop.init_text)
    if init is None or init.group(1) != loop.genvar:
        return None
    value = eval_const(init.group(2), env)
    if value is None:
        return None
    indices: list[int] = []
    seen: set[int] = set()
    while True:
        cond = eval_bool(loop.cond_text, {**env, loop.genvar: str(value)})
        if cond is None:
            return None
        if not cond:
            return tuple(indices)
        if value in seen or len(indices) >= MAX_ITERATIONS:
            return None
        seen.add(value)
        indices.append(value)
        nxt = _next_value(loop.step_text, loop.genvar, value, env)
        if nxt is None:
            return None
        value = nxt


def _render_guard(text: str, want: bool, scope: _Scope) -> str:
    spelled = " ".join(_substitute(text, scope).split())
    return spelled if want else f"!({spelled})"


def _unroll_items(
    items: tuple[GenerateItem, ...],
    scope: _Scope,
    instances: list[Instance],
    assigns: list[Assign],
) -> None:
    for item in items:
        if isinstance(item, Instance):
            instances.append(_unroll_instance(item, scope))
        elif isinstance(item, Assign):
            assigns.append(
                dataclasses.replace(
                    item,
                    lhs_text=_substitute(item.lhs_text, scope),
                    rhs_text=_substitute(item.rhs_text, scope),
                )
            )
        elif isinstance(item, GenerateLoop):
            _unroll_loop(item, scope, instances, assigns)
        else:
            _unroll_conditional(item, scope, instances, assigns)


def _unroll_loop(
    loop: GenerateLoop,
    scope: _Scope,
    instances: list[Instance],
    assigns: list[Assign],
) -> None:
    indices = loop_indices(loop, scope.env)
    iterations: list[tuple[str, str | None]] = (
        [(str(i), str(i)) for i in indices]
        if indices is not None
        else [(SYMBOLIC_INDEX, None)]
    )
    for index, value in iterations:
        prefix = f"{scope.prefix}{loop.label}[{index}]."
        env = {**scope.env, loop.genvar: value}
        inner = _with_locals(
            scope,
            prefix,
            loop.local_nets,
            loop.local_params,
            {**scope.genvars, loop.genvar: value if value is not None else index},
            env,
            scope.guards,
            loop.items,
        )
        _unroll_items(loop.items, inner, instances, assigns)


def _unroll_conditional(
    cond: GenerateConditional,
    scope: _Scope,
    instances: list[Instance],
    assigns: list[Assign],
) -> None:
    for arm in cond.arms:
        undecided: list[str] = []
        excluded = False
        for text, want in arm.guards:
            truth = eval_bool(text, scope.env)
            if truth is None:
                undecided.append(_render_guard(text, want, scope))
            elif truth != want:
                excluded = True
                break
        if excluded:
            continue
        prefix = f"{scope.prefix}{arm.label}."
        inner = _with_locals(
            scope,
            prefix,
            arm.local_nets,
            arm.local_params,
            scope.genvars,
            scope.env,
            scope.guards + tuple(undecided),
            arm.items,
        )
        _unroll_items(arm.items, inner, instances, assigns)


def elaborate(
    items: tuple[GenerateItem, ...], env: Env
) -> tuple[tuple[Instance, ...], tuple[Assign, ...]]:
    """Unroll a generate template into flat instances and assigns.

    Order is source order, iterations ascending. Two undecided arms
    may instantiate the same name under the same label (legal, since
    only one of them elaborates); the first is kept, so instance names
    stay unique.
    """
    instances: list[Instance] = []
    assigns: list[Assign] = []
    root = _Scope(prefix="", env=env, genvars={}, nets={}, guards=())
    _unroll_items(items, root, instances, assigns)
    seen: set[str] = set()
    unique: list[Instance] = []
    for inst in instances:
        if inst.name in seen:
            continue
        seen.add(inst.name)
        unique.append(inst)
    return tuple(unique), tuple(assigns)


def elaborate_module(module: Module, env: Env) -> Module:
    """``module`` with its generate template unrolled under ``env``.

    Returns ``module`` itself when it has no generate constructs or
    when ``env`` unrolls to exactly what it already carries, so a
    placement without a bound-changing override keeps sharing the
    table's object.
    """
    if not module.generate_body:
        return module
    instances, assigns = elaborate(module.generate_body, env)
    if instances == module.instances and assigns == module.assigns:
        return module
    return dataclasses.replace(module, instances=instances, assigns=assigns)
