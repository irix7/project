# smake evaluator: supported subset and extension direction

This document records the smake language subset implemented by
`scripts/runtime/smake.py` (issue #9), the semantic-parity repairs made for
issue #37, and the agreed extension direction for target/recipe execution
(issue #10). It is a public design record; no private SGI makefiles are
copied into the repository (ADR-0001).

## Purpose

The evaluator recovers the exact source/file selection the IRIX tree's leaf
makefiles make for each library variant, so the rebuilt archive contains the
same objects MIPSpro built. It does not run smake: smake only exists inside
the guest, and replacing the build system with a host driver is issue #10's
job.

## Supported language subset

The evaluator handles this slice of SGI smake:

| Construct | Semantics | Status |
|---|---|---|
| `NAME = value` | Recursively (deferred) expanded; value stored raw, expanded at reference time | supported |
| `NAME := value` | Immediately expanded; result stored | supported |
| `NAME ?= value` | Set only if `NAME` is not already defined; deferred | supported |
| `NAME += value` | Deferred append; raw text appended | supported |
| `$(NAME)` / `${NAME}` | Reference expansion, depth-limited to 32 | supported |
| `:M pattern` | Keep words matching a shell glob | supported |
| `:N pattern` | Drop words matching a shell glob | supported |
| `:S/old/new/` | Literal substitution, per word; `g` flag replaces all per word | supported |
| `:S/old/new/g` | Global per-word substitution | supported |
| `#if expr` / `#elif` / `#else` / `#endif` | Conditional inclusion with `==`, `!=`, `&&`, `||`, `!`, `( )`, `defined()` | supported |
| `defined(NAME)` / `defined NAME` | True when NAME is in the variable table | supported |
| `"$(VAR)"` in `#if` | Variable references inside quoted strings are expanded | supported |
| `include "file"` / `include <file>` | Quoted includes resolved through the `include` callback | supported |
| `include file` | Unquoted includes resolved through the `include` callback | supported |
| Missing include | Silently skipped when no `include` callback is provided | supported |
| `target: deps` | Target rule with dependency list | supported (issue #10) |
| tab-indented recipe | Recipe lines attached to the most recent target | supported (issue #10) |

## Semantic parity (issue #37)

The following defects in the original evaluator were repaired before any
recipe execution is claimed:

1. **Unquoted includes** — `include defs.mk` (no `<`/`"` delimiters) was
   silently skipped; it is now resolved through the `include` callback like
   quoted includes.
2. **Deferred `=` assignments** — `=` values were expanded at assignment
   time, breaking forward references; they are now stored raw and expanded
   when referenced.
3. **Quoted-variable conditions** — `"$(FLAG)"` inside `#if` was treated as
   literal text; the variable is now expanded before comparison.
4. **Per-word `:S` substitution** — substitution replaced only the first
   occurrence across the entire string; it now replaces the first occurrence
   in each word (or all occurrences per word with `g`).

Unsupported active constructs (e.g. unrecognised modifiers, shell functions,
multi-line shell recipes beyond simple command lines) raise `SmakeError`
rather than being silently skipped, so a build never silently selects a
different source set.

## Targets and recipes (issue #10)

The evaluator now parses target rules and tab-indented recipes:

```make
hinv: hinv.o
	hinvc -o hinv hinv.o
```

Results are exposed via `SmakeResult.targets`, a mapping of target name to
`{"deps": [...], "recipe": [...]}`. Variables remain accessible through the
dict interface (`result["CC"]`), so existing consumers (`leaf_variables`,
`manifest`) are unaffected.

### Extension direction

The agreed direction is to extend the evaluator incrementally, leaf by leaf:

1. **hinv** (first leaf): parse its stock `Makefile` target/recipe, recover
   the compile and link commands, and drive the host cross compiler through
   the same flags. One command builds hinv from its selected stock target
   with no manual per-run compiler flags.
2. **echo** (second leaf): the same interface builds echo, demonstrating that
   rule/dependency/automatic-variable/recipe support works on a second
   concrete leaf.
3. **Native-vs-driver comparison**: expanded variables, source/object lists
   and translated flags are compared between native smake and the host
   driver, with raw private material retained privately (not committed).

Automatic variables (`$@`, `$<`, `$*`, `$^`) and recipe execution are not
yet implemented; they are the next increment after hinv and echo parse
cleanly. Unsupported active constructs in a selected leaf fail clearly with
`SmakeError` rather than being skipped.

## Boundaries

- No GNU-make rewrite, no full smake clone, no reinterpretation of the
  user-selected build strategy.
- No tree text is copied into the repository (ADR-0001); real makefiles are
  read only at build time.
- All snippets in tests are synthetic; no proprietary makefile content is
  used in public fixtures.
