# Publication boundary: inventory and attestation

ADR-0001 limits public output to the toolchain itself: GCC and binutils
patches, build scripts and documentation. Nothing from the private IRIX tree
is published — no sysroot, headers, compiled binaries, disk images or tree
excerpts. This note records the publication-policy inventory taken for issue
#23: the confirmed finding and its resolution, the method, the current-tree
result, the public-history result, and the referral of any history
remediation to issue #36.

## The confirmed finding and its resolution

Audit S1 (snapshot `212013a`) found that `scripts/runtime/rebuild-libc.py`
embedded a statement from the private tree as the argument of an erratum
matcher; the statement appeared verbatim in the private tree. A bullet in
`docs/runtime.md` also repeated the called routine's name and the call shape.

This branch removes both. `compat.fix_unterminated_statement(text)` now takes
only the source text and recognises the construct by syntax: a call-expression
line inside a block, without its terminating semicolon, followed by a complete
statement or a closing brace. It repairs exactly one unambiguous candidate and
raises `CompatError` for none or for several, so it cannot alter a different
statement by accident. The driver's erratum table calls the generic form.

A read-only probe against the private tree (working copy `822f4fc`) confirmed
the matcher finds exactly one candidate in the erratum source, inserts exactly
one semicolon, preserves the line count and leaves every other line unchanged.
The probe reported a line number and hashes only; no private text was copied
into the repository or into any output. Regression tests use independently
authored synthetic snippets (`scripts/runtime/test-compat.py`), and no private
source fixture is committed.

## Method

The inventory compares digests, never text:

- **Private side.** Every text-like file under the private tree (no NUL byte
  in the first 8 KiB; decodes as UTF-8), excluding `.git`. Each line is
  stripped and kept at length >= 40; a 16-byte SHA-256 digest is indexed per
  line. A digest seen in more than one private file is marked *shared*
  (licence texts, separator rules and other boilerplate).
- **Public side, current tree.** Every tracked file. Candidate strings are the
  trimmed line, the line with diff/quote markers stripped, quoted literal
  contents, single-line block-comment bodies and line-comment bodies; each is
  digested and looked up. The whole-file SHA-256 is compared as well.
- **Public side, history.** Every unique blob reachable from `main`, the
  canonical public history, gets the same candidate extraction and whole-file
  comparison; matches record the blob, its paths and the commits that reach
  it. In-flight issue branches are other work and are not part of this
  attestation.
- **Tracked artefacts.** Tracked files are checked for NUL bytes, ELF and ar
  magic; the ignore rules are checked with `git check-ignore`.

**Scope and limits.** Matching is exact after trimming; reformatted, reflowed,
partially quoted or sub-40-character excerpts are outside the scan. A match
against a line that is also public third-party text is classified as such
rather than as a tree excerpt, and the *shared* marker only approximates
distinctiveness. The inventory is a bounded, read-only inspection of text, not
a licence determination, and it takes no view on material already published
outside this repository.

## Current-tree result

Snapshot 2026-10-05, base `212013a`, 66 tracked files including this document;
private working copy `822f4fc`, 30,517 text files, 3,761,598 long lines,
2,426,399 unique line digests (637,639 shared).

- **Whole-file matches: none.**
- **The audit S1 excerpt: absent** from the tracked tree (removed by this
  branch; its line digest is
  `f5ba5ed9aaaffc19d2bf8bcf60dde4cb`, matching private
  `irix/lib/libc/src/locale/sgi_ffmtmsg.c:23`).
- **Line-digest matches: 89 raw.** 73 are shared boilerplate (GNU licence text
  also carried by the private tree's own `COPYING`/`COPYING.LIB` copies,
  repeated comment rules, shared readline configuration lines). The 16
  unshared matches fall into two classes:
  - 11 occurrences of one line of GPLv3 boilerplate in
    `patches/gcc-16.2/0006-libstdcxx-irix-os-layer.patch`, matching the
    private tree's bundled `gnum4` licence copy: public GNU text on both
    sides, not an SGI excerpt;
  - two `inst` progress messages matched by `scripts/rig/install-driver.py`,
    `scripts/rig/oracle-driver.py` and `scripts/rig/test-oracle-driver.py`,
    matching the private installation guide. These are short
    console-interface strings the rig must recognise to drive the proprietary
    installer; they are not program-source excerpts. They are recorded here as
    findings metadata for the maintainer and are not removed by this issue:
    whether these interface strings may remain, and every history question,
    is a maintainer judgement referred to issue #36
    (<https://github.com/irix7/project/issues/36>). This document attests
    what was found; it does not grant the exception.

No tracked sysroot, generated SGI message/header content or rebuilt binary was
found: no tracked file carries ELF or ar magic or NUL bytes,
`scripts/rig/sysroot.files` is a path manifest rather than a sysroot copy, and
the `scripts/runtime/include/msgs` headers are project reconstructions carrying
catalogue keys only. `.gitignore` covers `.scratch/`, and `git check-ignore`
confirms that generated runtime artefacts there are ignored.

## Public-history result

Snapshot 2026-10-05, history reachable from `main` at the branch base
(`212013a`): 14 commits, 93 unique blobs, 90 raw line-digest matches — 73
shared boilerplate and 17 unshared (11 GPLv3-boilerplate occurrences, 5
installer-interface occurrences and the S1 statement). Whole-file matches:
none. The S1 statement survives in one blob (`86dec92d...`) of
`scripts/runtime/rebuild-libc.py`, reachable from `212013a` and `4a73993`.

History is inventoried, never rewritten. Any decision to rewrite, filter,
annotate or leave the historical excerpt belongs to the maintainer and is
referred to issue #36:
<https://github.com/irix7/project/issues/36>.
