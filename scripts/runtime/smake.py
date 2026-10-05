#!/usr/bin/env python3
"""A tiny evaluator for the slice of SGI smake this project needs (issue #9).

The IRIX tree's libraries are built by ``smake`` (IRIX's make), whose leaf
makefiles use a small C-preprocessor-like language: ``#if``/``#elif``/
``#else``/``#endif``, ``#include``, ``=``/``+=`` assignments and ``$(VAR)``
references with ``:M`` (keep matching words), ``:N`` (drop matching words) and
``:S/from/to/`` (substitute) modifiers.

The runtime rebuild does not run smake: the new toolchain is a host cross,
smake only exists inside the guest, and replacing the build system is issue
#10's job. But the makefiles enumerate exactly which sources belong to which
library variant, and that selection is release data worth preserving. This
module evaluates just enough smake to recover those lists, so the rebuilt
archive contains the same objects MIPSpro built.

It deliberately has no makefile-language ambitions beyond that slice: no
rules, no targets, no functions, no shell. Anything unrecognised is skipped.

No tree text is copied into the repository (ADR-0001); this file only knows
the syntax.
"""

from __future__ import annotations

import fnmatch
import re
from typing import Callable, Optional

__all__ = ["SmakeError", "evaluate", "expand", "filter_match", "substitute"]


class SmakeError(Exception):
    """The makefile slice used something this evaluator cannot represent."""


_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ASSIGN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(\+=|:=|\?=|=)\s?(.*)$")
_COND = re.compile(r"^#\s*(if|elif|else|endif)\b(.*)$")
_INCLUDE = re.compile(r'^#?\s*include\s*([<"])([^>"]*)[>"]\s*$')


def _find_close(text: str, start: int) -> int:
    """Index of the delimiter matching the one at ``start`` (depth-aware)."""
    closers = {"(": ")", "{": "}"}
    stack = [closers[text[start]]]
    i = start + 1
    while i < len(text):
        ch = text[i]
        if ch in closers:
            stack.append(closers[ch])
        elif ch in ")}":
            if not stack or ch != stack[-1]:
                raise SmakeError(f"unbalanced reference: {text[start - 1:]!r}")
            stack.pop()
            if not stack:
                return i
        i += 1
    raise SmakeError(f"unterminated reference: {text[start - 1:]!r}")


def _parse_modifiers(mods: str, value: str, variables: dict, depth: int) -> str:
    """Apply a ``:M``/``:N``/``:S`` chain to a variable's words or value."""
    i = 0
    while i < len(mods):
        if mods[i] != ":":
            raise SmakeError(f"malformed modifier chain: {mods!r}")
        i += 1
        if i >= len(mods):
            raise SmakeError(f"trailing ':' in modifier chain: {mods!r}")
        kind = mods[i]
        if kind in ("M", "N"):
            i += 1
            end = mods.find(":", i)
            if end == -1:
                end = len(mods)
            pattern = expand(mods[i:end], variables, depth + 1)
            i = end
            value = filter_match(value, pattern, keep=kind == "M")
        elif kind == "S":
            i += 1
            if i >= len(mods):
                raise SmakeError(f"missing delimiter in substitution: {mods!r}")
            delim = mods[i]
            i += 1
            end = mods.find(delim, i)
            if end == -1:
                raise SmakeError(f"unterminated substitution: {mods!r}")
            pattern = expand(mods[i:end], variables, depth + 1)
            i = end + 1
            end = mods.find(delim, i)
            if end == -1:
                raise SmakeError(f"unterminated substitution: {mods!r}")
            replacement = expand(mods[i:end], variables, depth + 1)
            i = end + 1
            end = i
            while end < len(mods) and mods[end] != ":":
                end += 1
            flags = mods[i:end]
            i = end
            value = substitute(value, pattern, replacement, flags)
        else:
            # Unsupported modifiers are ignored rather than guessed at; the
            # tree's leaf makefiles use only M, N and S.
            end = mods.find(":", i)
            i = len(mods) if end == -1 else end
    return value


def substitute(value: str, pattern: str, replacement: str, flags: str = "") -> str:
    """smake ``:S`` substitution. ``pattern`` is literal here, not a regex."""
    if pattern == "":
        return value
    if "g" in flags:
        return value.replace(pattern, replacement)
    return value.replace(pattern, replacement, 1)


def filter_match(value: str, pattern: str, keep: bool = True) -> str:
    """smake ``:M`` (keep) or ``:N`` (drop) words matching a shell glob."""
    return " ".join(
        w for w in value.split() if fnmatch.fnmatchcase(w, pattern) is keep
    )


def _apply_reference(inner: str, variables: dict, depth: int) -> str:
    match = _NAME.match(inner)
    if not match:
        raise SmakeError(f"reference has no variable name: $({inner})")
    name = match.group(0)
    value = expand(variables.get(name, ""), variables, depth + 1)
    mods = inner[len(name):]
    if mods:
        value = _parse_modifiers(mods, value, variables, depth)
    return value


def expand(text: str, variables: dict, depth: int = 0) -> str:
    """Expand ``$(VAR)`` / ``${VAR}`` references and their :M/:N/:S chain."""
    if depth > 32:
        raise SmakeError("reference expansion too deep (cycle?)")
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "$" and i + 1 < len(text) and text[i + 1] in "({":
            close = _find_close(text, i + 1)
            out.append(_apply_reference(text[i + 2:close], variables, depth))
            i = close + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


class _ConditionError(SmakeError):
    pass


class _ConditionParser:
    """Recursive-descent parser for the ``#if`` expression slice in use."""

    _OPS = ("&&", "||", "==", "!=")

    def __init__(self, text: str, variables: dict):
        self.variables = variables
        self.tokens = self._tokenise(text, variables)
        self.pos = 0

    def _tokenise(self, text: str, variables: dict) -> list:
        tokens: list = []
        i = 0
        while i < len(text):
            ch = text[i]
            if ch.isspace():
                i += 1
                continue
            op = next((o for o in self._OPS if text.startswith(o, i)), None)
            if op:
                tokens.append((op, op))
                i += len(op)
                continue
            if ch in "()!":
                tokens.append((ch, ch))
                i += 1
                continue
            if ch == '"':
                end = text.find('"', i + 1)
                if end == -1:
                    raise _ConditionError(f"unterminated quote in #if: {text!r}")
                tokens.append(("value", text[i + 1:end]))
                i = end + 1
                continue
            if ch == "$" and i + 1 < len(text) and text[i + 1] in "({":
                close = _find_close(text, i + 1)
                tokens.append(("value", expand(text[i:close + 1], variables)))
                i = close + 1
                continue
            end = i
            while end < len(text) and not text[end].isspace() and text[end] not in '()!"':
                end += 1
            word = text[i:end]
            tokens.append(("defined" if word == "defined" else "value", word))
            i = end
        tokens.append(("eof", ""))
        return tokens

    def _peek(self):
        return self.tokens[self.pos]

    def _next(self):
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def parse(self) -> bool:
        value = self._or()
        if self._peek()[0] != "eof":
            raise _ConditionError(f"trailing tokens in #if: {self.tokens!r}")
        return value

    def _or(self) -> bool:
        value = self._and()
        while self._peek()[0] == "||":
            self._next()
            right = self._and()
            value = value or right
        return value

    def _and(self) -> bool:
        value = self._unary()
        while self._peek()[0] == "&&":
            self._next()
            right = self._unary()
            value = value and right
        return value

    def _unary(self) -> bool:
        kind, _ = self._peek()
        if kind == "!":
            self._next()
            return not self._unary()
        if kind == "(":
            self._next()
            value = self._or()
            if self._next()[0] != ")":
                raise _ConditionError("missing ')' in #if")
            return value
        return self._comparison()

    def _comparison(self) -> bool:
        left = self._atom()
        kind, _ = self._peek()
        if kind in ("==", "!="):
            self._next()
            right = self._atom()
            equal = left == right
            return equal if kind == "==" else not equal
        return _truthy(left)

    def _atom(self) -> str:
        kind, value = self._next()
        if kind == "defined":
            if self._peek()[0] == "(":
                self._next()
                name_kind, name = self._next()
                if name_kind != "value":
                    raise _ConditionError("defined() needs a variable name")
                if self._next()[0] != ")":
                    raise _ConditionError("missing ')' after defined(")
            else:
                name_kind, name = self._next()
                if name_kind != "value":
                    raise _ConditionError("defined needs a variable name")
            return "1" if name in self.variables else ""
        if kind not in ("value",):
            raise _ConditionError(f"unexpected token in #if: {value!r}")
        return value


def _truthy(value: str) -> bool:
    return value != "" and value != "0"


def evaluate(
    text: str,
    variables: Optional[dict] = None,
    include: Optional[Callable[[str], Optional[str]]] = None,
) -> dict:
    """Evaluate a smake fragment, returning the resulting variables.

    ``include`` resolves ``#include`` paths; returning ``None`` skips the
    include. Anything that is not an assignment, conditional or include (rules,
    commands, comments) is ignored.
    """
    result = dict(variables or {})
    _process(text, result, include, depth=0)
    return result


def _logical_lines(text: str):
    """Join backslash-continued physical lines into logical lines."""
    pending: Optional[str] = None
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if pending is not None:
            line = pending + " " + line.lstrip()
            pending = None
        if line.endswith("\\"):
            pending = line[:-1]
            continue
        yield line
    if pending is not None:
        yield pending


def _process(text: str, variables: dict, include, depth: int) -> None:
    if depth > 32:
        raise SmakeError("include nesting too deep")
    # Each frame is [parent_active, taken, branch_active].
    stack: list = []
    active = True

    for line in _logical_lines(text):
        cond = _COND.match(line)
        if cond:
            directive, rest = cond.group(1), cond.group(2)
            if directive == "if":
                parent = active
                value = _ConditionParser(rest, variables).parse() if parent else False
                stack.append([parent, bool(value), bool(value)])
                active = parent and bool(value)
            elif directive == "elif":
                if not stack:
                    raise SmakeError("#elif without #if")
                parent, taken, _ = stack[-1]
                value = (
                    not taken and _ConditionParser(rest, variables).parse()
                    if parent
                    else False
                )
                stack[-1] = [parent, taken or bool(value), bool(value)]
                active = parent and bool(value)
            elif directive == "else":
                if not stack:
                    raise SmakeError("#else without #if")
                parent, taken, _ = stack[-1]
                stack[-1] = [parent, True, not taken]
                active = parent and not taken
            else:  # endif
                if not stack:
                    raise SmakeError("#endif without #if")
                parent, _, _ = stack.pop()
                active = parent
            continue

        if not active:
            continue

        inc = _INCLUDE.match(line)
        if inc:
            if include is None:
                continue
            path = expand(inc.group(2), variables)
            body = include(path)
            if body is not None:
                _process(body, variables, include, depth + 1)
            continue

        if line.lstrip().startswith("#"):
            # #ident, comments and other preprocessor noise.
            continue

        assign = _ASSIGN.match(line)
        if assign:
            name, op, value = assign.group(1), assign.group(2), assign.group(3)
            value = expand(value, variables)
            if op == "?=":
                if name not in variables:
                    variables[name] = value
            elif op == "+=":
                variables[name] = (variables.get(name, "") + " " + value).strip()
            else:
                variables[name] = value

    if stack:
        raise SmakeError("unterminated #if")


# ---------------------------------------------------------------------------
# Tree driver: read the real leaf makefiles and emit a build manifest.
# ---------------------------------------------------------------------------

# The library source selection for the project's static proof. libc_32_M2_ns
# is the o32/mips2 nonshared archive the releasedefs' 32_M2 style builds; the
# tree's SUBDIRS order is kept because archive member order follows it.
# libm has no source tree of its own in this release (the maths code lives in
# libc/src/math); the maths archive is therefore the same leaf.
DEFAULT_LIBRARY = "libc_32_M2_ns.a"
DEFAULT_OBJECT_STYLE = "32_M2"

# Variables libleafdefs would have supplied before the leaf's own body.
_BASE_VARIABLES = {
    "ROOT": "/",
    "INCLDIR": "/usr/include",
    "DEPTH": "..",
    "TOP": "../../",
    "VCC": "MIPSpro",
    "LIBRARY_CDEFS": "-DNDEBUG -U__MATH_HAS_NO_SIDE_EFFECTS "
    "-D_SGI_COMPILING_LIBC -U__INLINE_INTRINSICS",
    "LIBRARY_CINCS": "-I../../inc",
}


def _include_resolver(directory):
    def resolve(path: str) -> Optional[str]:
        candidate = directory / path
        if candidate.is_file():
            return candidate.read_text()
        return None

    return resolve


def leaf_variables(tree: str, subdir: str, library: str, object_style: str) -> dict:
    """Evaluate one leaf makefile for a library variant."""
    directory = _source_dir(tree) / subdir
    makefile = directory / "Makefile"
    if not makefile.is_file():
        raise SmakeError(f"no leaf makefile: {makefile}")
    variables = dict(_BASE_VARIABLES)
    variables["LIBRARY"] = library
    variables["OBJECT_STYLE"] = object_style
    return evaluate(
        makefile.read_text(), variables, include=_include_resolver(directory)
    )


def _source_dir(tree: str):
    from pathlib import Path

    return Path(tree) / "irix" / "lib" / "libc" / "src"


def manifest(tree: str, library: str, object_style: str):
    """Yield ``(kind, subdir, name, value)`` rows for a library variant.

    ``kind`` is ``c``, ``s`` or ``var``. The special ``csu`` leaf has no
    CFILES/ASFILES (its startup objects are assembled in place); it is
    reported as a var row with the marker ``__CSU__``.
    """
    root_variables = evaluate(
        (_source_dir(tree) / "Makefile").read_text(),
        {"ROOT": "/", "DEPTH": ".", "TOP": ""},
        include=_include_resolver(_source_dir(tree)),
    )
    subdirs = root_variables.get("SUBDIRS", "").split()
    for subdir in subdirs:
        variables = leaf_variables(tree, subdir, library, object_style)
        if subdir == "csu":
            yield ("var", subdir, "__CSU__", "crt1.o mcrt1.o crtn.o")
        for name in variables.get("CFILES", "").split():
            yield ("c", subdir, name, "")
        for name in variables.get("ASFILES", "").split():
            yield ("s", subdir, name, "")
        for key in (
            "SUBDIR_CDEFS",
            "SUBDIR_CINCS",
            "SUBDIR_ASINCS",
            "SUBDIR_COPTS",
            "LCDEFS",
            "LCINCS",
            "LASDEFS",
            "LASINCS",
            "QUAD_WORD_CFILES",
            "QUAD_WORD_ASFILES",
        ):
            value = variables.get(key, "")
            if value:
                yield ("var", subdir, key, value)


def _cli() -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="smake.py",
        description="Evaluate the tree's smake leaf makefiles (issue #9).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    man = sub.add_parser("manifest", help="emit the build manifest as TSV")
    man.add_argument("--tree", required=True, help="IRIX tree root")
    man.add_argument("--library", default=DEFAULT_LIBRARY)
    man.add_argument("--object-style", default=DEFAULT_OBJECT_STYLE)

    args = parser.parse_args()
    if args.command == "manifest":
        for kind, subdir, name, value in manifest(
            args.tree, args.library, args.object_style
        ):
            print("\t".join((kind, subdir, name, value)))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
