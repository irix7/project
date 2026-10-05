#!/usr/bin/env python3
"""Stream translations for the IRIX tree's MIPSpro-era sources (issue #9).

The runtime rebuild compiles SGI's stock sources with GCC 16.2. A few
constructs the tree uses are MIPSpro-specific and need translating, the same
way the flag tables translate MIPSpro options. None of this edits the tree:
the translations happen in memory and the build writes its own copies.

The constructs, each with the reason it exists:

``#pragma weak A = B``
    MIPSpro resolved the names in the pragma before ``synonyms.h`` renamed
    them; GNU cpp macro-expands the pragma first, so ``tcgetattr =
    _tcgetattr`` becomes ``_tcgetattr = _tcgetattr`` and GCC rejects the
    redefinition. Emitting an assembler-level weak alias preserves MIPSpro's
    meaning (the public name becomes a weak alias of the internal one).

``.weakext A, B``
    GNU as treats the two-operand form as defining ``A`` as an alias of
    ``B``; when the same file then defines ``A`` outright (a MIPSpro-legal
    merge of a weak alias with a local definition) GNU as reports a
    redefinition. Where the file defines the symbol, the alias operand is
    dropped and the local definition wins, as under MIPSpro.

``cvt.d.w $fN`` / ``cvt.d.l $fN``
    MIPSpro's assembler allowed the one-register shorthand for a
    same-register conversion; GNU as requires both operands spelled out.

Double-quoted single characters in immediate operands
    ``li t4, "0"`` is MIPSpro assembler syntax for the character constant;
    GNU as wants ``'0'``.

``.text .init`` / ``.text .fini``
    MIPSpro's subsection syntax; translated to a GNU section directive.

``SYS_ xpg4_recvmsg``
    A GNU traditional-cpp nested-macro defect: the ``SYS_/**/y`` paste in
    ``PSEUDO`` expands with a space for this one call and the assembler never
    sees the macro. The numeric value from the sysroot's ``sys.s`` is
    substituted (227 + the 1000 SYSVoffset).

``sys/t6api_private.h``
    Included with a ``sys/`` prefix but shipped flat in the kernel tree; the
    build stages a copy under the expected name (see rebuild-libc.py).

No tree text is copied into the repository (ADR-0001).
"""

from __future__ import annotations

import re

__all__ = [
    "CompatError",
    "fix_unterminated_statement",
    "retarget_knr_definition",
    "rewrite_asm_source",
    "rewrite_c_source",
]


class CompatError(Exception):
    """A requested source erratum does not apply."""


_PRAGMA_WEAK = re.compile(r"^[ \t]*#pragma[ \t]+weak[ \t]+(.+?)[ \t]*$", re.M)
_WEAKEXT = re.compile(r"\.weakext\s+([A-Za-z_][\w.]*)\s*,\s*([A-Za-z_][\w.]*)")
_CVT_SHORTHAND = re.compile(r"\b(cvt\.[a-z]+\.[a-z]+)\s+(\$f\d+)\s*(?=#|$)", re.M)
_CHAR_IMMEDIATE = re.compile(
    r"^([ \t]*(?:li|addi|addiu|ori|andi|xori|slti|sltiu)[ \t]+\S+,[ \t]*)\"([^\"\n])\"",
    re.M,
)
_TEXT_SUBSECTION = re.compile(r"^([ \t]*)\.text[ \t]+\.(init|fini)[ \t]*$", re.M)
_SYS_PASTE_CASUALTY = re.compile(r"\bSYS_[ \t]+xpg4_recvmsg\b")

# The unterminated-statement erratum is recognised by syntax, never by the
# tree's source text (ADR-0001): a cast-qualified or plain call expression,
# with balanced parentheses, inside a block.
_CAST = r"\(\s*[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*\s*\**\s*\)"
_CALL_STATEMENT = re.compile(r"^(?:" + _CAST + r"\s*)*([A-Za-z_]\w*)\s*\(.*\)$")
_CONTROL_KEYWORDS = frozenset({"if", "for", "while", "switch", "return", "sizeof"})


def retarget_knr_definition(text: str, name: str, implementation: str) -> str:
    """Give a K&R definition a private name and alias the public one to it.

    A K&R definition whose public prototype is variadic (POSIX ``mq_open``)
    is a constraint violation GCC rejects whether or not it is variadic.
    Moving the definition to a private name and emitting an assembler global
    alias keeps the public symbol and the body identical.
    """
    pattern = re.compile(r"^" + re.escape(name) + r"\(", re.M)
    if not pattern.search(text):
        raise CompatError(f"no K&R definition of {name}() found")
    text = pattern.sub(implementation + "(", text, count=1)
    return (
        f'__asm__(".globl {name}\\n.set {name}, {implementation}");\n' + text
    )


def _line_states(lines: list) -> list:
    """Brace depth and block-comment state at the start of each line."""
    states = []
    depth = 0
    in_comment = False
    for line in lines:
        states.append((depth, in_comment))
        j = 0
        while j < len(line):
            if in_comment:
                if line.startswith("*/", j):
                    in_comment = False
                    j += 2
                else:
                    j += 1
            elif line.startswith("//", j):
                break
            elif line.startswith("/*", j):
                in_comment = True
                j += 2
            elif line[j] in "\"'":
                quote = line[j]
                j += 1
                while j < len(line) and line[j] != quote:
                    j += 2 if line[j] == "\\" else 1
                j += 1
            else:
                if line[j] == "{":
                    depth += 1
                elif line[j] == "}":
                    depth -= 1
                j += 1
    return states


def _parens_balanced(line: str) -> bool:
    """True when the line's parentheses nest and close, ignoring literals."""
    depth = 0
    j = 0
    while j < len(line):
        if line.startswith("//", j):
            break
        if line.startswith("/*", j):
            end = line.find("*/", j + 2)
            j = len(line) if end < 0 else end + 2
        elif line[j] in "\"'":
            quote = line[j]
            j += 1
            while j < len(line) and line[j] != quote:
                j += 2 if line[j] == "\\" else 1
            j += 1
        elif line[j] == "(":
            depth += 1
            j += 1
        elif line[j] == ")":
            depth -= 1
            if depth < 0:
                return False
            j += 1
        else:
            j += 1
    return depth == 0


def fix_unterminated_statement(text: str) -> str:
    """Append the semicolon a source upload left off a call statement.

    The candidate is found syntactically: a call-expression line inside a
    block, without its terminating semicolon, followed by a complete statement
    or a bare closing brace. Exactly one candidate must exist; none, or
    several, raises CompatError rather than guessing. The tree's statement
    text never enters the repository (ADR-0001).
    """
    lines = text.split("\n")
    states = _line_states(lines)
    significant = [i for i, line in enumerate(lines) if line.strip()]
    following = dict(zip(significant, significant[1:]))
    candidates = []
    for i, line in enumerate(lines):
        depth, in_comment = states[i]
        if depth <= 0 or in_comment:
            continue
        stripped = line.strip()
        if not stripped or stripped.endswith(";"):
            continue
        if stripped[:2] in ("//", "/*") or stripped.startswith("*"):
            continue
        match = _CALL_STATEMENT.match(stripped)
        if not match or match.group(1) in _CONTROL_KEYWORDS:
            continue
        if not _parens_balanced(stripped):
            continue
        after = lines[following[i]].strip() if i in following else ""
        if after == "}" or (
            after != ";" and after.endswith(";") and not after.startswith("}")
        ):
            candidates.append(i)
    if not candidates:
        raise CompatError("no unterminated statement candidate found")
    if len(candidates) > 1:
        raise CompatError(
            f"{len(candidates)} unterminated statement candidates; refusing to guess"
        )
    index = candidates[0]
    line = lines[index]
    body = line.rstrip()
    lines[index] = body + ";" + line[len(body):]
    return "\n".join(lines)


def rewrite_c_source(text: str) -> str:
    """Translate MIPSpro C constructs GNU cpp/GCC mis-handle."""

    def pragma(match: re.Match) -> str:
        body = match.group(1)
        if "=" in body:
            name, _, target = body.partition("=")
            name, target = name.strip(), target.strip()
            if not name or not target:
                return match.group(0)
            return f'__asm__(".weak {name}\\n.set {name}, {target}");'
        return f'__asm__(".weak {body.strip()}");'

    return _PRAGMA_WEAK.sub(pragma, text)


def _defined_in(text: str, symbol: str) -> bool:
    """True when the assembly defines ``symbol`` locally.

    The check runs on preprocessed text, where LEAF/NESTED have expanded to
    labels (``memset:;`` mid-line), so a bare label match is the reliable
    signal.
    """
    return bool(
        re.search(
            r"(?:LEAF|XLEAF|NESTED|FRAME|PSEUDO)\s*\(\s*"
            + re.escape(symbol)
            + r"\s*[,)]",
            text,
        )
        or re.search(r"(?<![\w.])" + re.escape(symbol) + r"\s*:", text)
    )


def rewrite_asm_source(text: str) -> str:
    """Translate MIPSpro assembler syntax GNU as rejects."""

    def weakext(match: re.Match) -> str:
        symbol, alias = match.group(1), match.group(2)
        if _defined_in(text, symbol):
            return f".weakext\t{symbol}"
        return match.group(0)

    text = _WEAKEXT.sub(weakext, text)
    text = _CVT_SHORTHAND.sub(lambda m: f"{m.group(1)}\t{m.group(2)},{m.group(2)}", text)
    text = _CHAR_IMMEDIATE.sub(lambda m: f"{m.group(1)}'{m.group(2)}'", text)
    text = _TEXT_SUBSECTION.sub(
        lambda m: f'{m.group(1)}.section .{m.group(2)},"ax",@progbits', text
    )
    text = _SYS_PASTE_CASUALTY.sub("1227", text)
    return text
