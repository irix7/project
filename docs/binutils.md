# Binutils for the IRIX cross (issue #21)

`scripts/build-toolchain.sh` builds GNU binutils 2.47 for
`mips-sgi-irix6.5`; vanilla-first was tried and **rejected in the
controlled guest smoke** (a vanilla 2.47 o32 hello dies at startup), so a
re-derived IRIX series now lives in `patches/binutils-2.47/` as a
candidate under guest validation. Until a candidate passes o32 and n32 in
the guest, the selector still builds the vanilla recipe and
`--binutils 2.20.1` remains the known-good pdaxrom seed fallback. This
document records the pins, the in-guest divergence, the upstream
archaeology and the candidate signatures.

## Pin and provenance

| item | value |
| --- | --- |
| release | GNU binutils 2.47 (`GNU Binutils 2.47.20260726`) |
| tarball | `binutils-2.47.tar.xz`, `https://ftp.gnu.org/gnu/binutils/` |
| sha256 | `154ab23b60070e8f27013c22977f1129425d67d1e8acd6e13010e617811e4cff` |
| sha512 | `3126a1064374d8da40d4d70630c204ed1e75d542c447d53fca9778c7ceff095c28e9b445e15a313fef9729082d7966471ee6b5b715d479aa6d568528743e1d98` |
| checksum list | `https://sourceware.org/pub/binutils/releases/sha512.sum` |
| SGUG reference | binutils 2.23.2 + `binutils2_23.sgifixes.patch`, built for comparison |

The 2.20.1 pin and its two pdaxrom patches are unchanged. The SGUG
2.23.2 reference (`binutils-2.23.2.tar.bz2`,
sha256 `fe914e56fed7a9ec2eb45274b1f2e14b0d8b4f41906a5194eac6883cfe5c1097`;
`binutils2_23.sgifixes.patch`,
sha256 `03dd9d2d7d9ccee1f716291e67333ad4a4192a081ffd563e3a10abf27ef72ef9`)
is a diagnostic build in scratch, not a project dependency; SGUG's own
toolchain is the known-good modern IRIX binutils deployment.

`gas`, `ld` and `bfd` configure and build from the release for the target,
so the original removal premise stays disproved at build level; only the
runtime divergence below needs patches.

## In-guest divergence (integrator evidence)

Controlled guest smokes of `oracle/hello.c` linked with the 16.2 cross,
using the generated specs file to select the assembler and linker:

| variant | result |
| --- | --- |
| baseline prefix (configured 2.20.1 as/ld) | passes o32 |
| control specs pointing at the configured 2.20.1 | passes o32 |
| vanilla 2.47 | links, guest dies with SIGSEGV (exit 139) before output |
| onre's partial port (prefix-2.47-onre) | guest dies with SIGBUS (exit 138) |
| cand-a (SGUG GOT, patch 0001) | o32 SIGBUS (138); n32 passes |
| cand-b (`_gp`, patch 0002) | o32 SIGSEGV (139); n32 SIGSEGV |
| cand-c (0001+0002) | o32 SIGBUS (138); n32 passes |

The integrator's root-cause lead for the o32 crash: the working 2.20.1
output carries the `.rld_map` section and the map slot is inside the RW
segment's zero-filled tail, while the 2.47 variants have no `.rld_map`,
`DT_MIPS_RLD_MAP` names a slot in the `.sbss` tail, and (on the
guest-tested binaries) the RW segment's `p_memsz` equals `p_filesz`, so
IRIX rld's startup write to the map pointer faults. That is the
divergence patch 0003 restores; see the archaeology below. Note that on
this host the 2.47 links already report `p_memsz 0xa0` with the slot
inside `.sbss`; the fix is judged by the guest, and the readelf rows
below record both.

The same o32 hello's readelf signature:

| variant | `_gp` | `_gp_disp` | MIPS_LOCAL_GOTNO | MIPS_GOTSYM | MIPS_HIPAGENO | MIPS_SYMTABNO |
| --- | --- | --- | --- | --- | --- | --- |
| 2.20.1 baseline | `10008000` NOTYPE GLOBAL ABS | present (symtab) | 9 | 0xb | 7 | 24 |
| SGUG 2.23.2 reference | `10008000` NOTYPE LOCAL ABS | present (symtab) | 9 | 0xb | 7 | 24 |
| vanilla 2.47 | `10008000` NOTYPE LOCAL, section 18 (.got) | absent | 13 | 0xd | 11 | 22 |
| onre 2.44 partial port | `10008000` NOTYPE LOCAL, section 18 (.got) | absent | 9 | 0x9 | 7 | 22 |

`.MIPS.options` and `.reginfo` are byte-identical across all of these
(gp value `0x10008000` for o32, `0x10018b40` for n32), so the gp *value*
is not the divergence; the symbol binding and GOT/symbol-table shape are.
Note that `_gp` and `_gp_disp` appear only in `.symtab`, never in
`.dynsym`, in the working 2.20.1 and SGUG links; the loaded image differs
in the GOT/dynamic tags and in the dynamic symbol table.

## Upstream archaeology

### `_gp` was made hidden (2012)

Commit `9e8082845f85cc1cb6be434177aa4d59e00663ff` ("ld/: Make _gp
hidden", Maciej W. Rozycki, 2012-08-06) wrapped `_gp` in `HIDDEN()` in
`ld/emulparams/elf32bmip.sh`, `ld/emulparams/elf32bmipn32-defs.sh`,
`ld/emulparams/elf32mipswindiss.sh` and `ld/scripttempl/mips.sc`. Linux
MIPS does not want `_gp` exported; IRIX's runtime linker and the classic
linker contract expect the absolute `_gp`. The change post-dates nothing
in SGUG's patch (SGUG 2.23.2 has hidden `_gp`, but still ABS and working
on IRIX), yet 2.47 additionally emits it section-relative in `.got`,
which the candidate series restores to `GLOBAL ABS`.

### `_gp_disp` left the symbol tables (2018)

Commit `3be08ea4728b56d35e136af4e6fd3086ade17764` ("BFD: Prevent writing
the MIPS _gp_disp symbol into symbol tables", Simon Atanasyan, 2018-05-03)
removed the `_gp_disp` special cases from `mips_elf_output_extsym` and
`_bfd_mips_elf_finish_dynamic_symbol` and added
`elf32_mips_fixup_symbol`, which hides `_gp_disp` for o32. In the working
2.20.1 output `_gp_disp` is undefined GLOBAL in `.symtab` only (it is not
in `.dynsym` and not loaded); 2.47 drops the entry. A scratch probe
reverted the commit's three changes under `SGI_COMPAT`/IRIX and the
`.symtab` entry still did not reappear (the observable removal has a
different, generic cause), and because `.symtab` is not part of the
loaded image the reverted hunks could not be demonstrated. No hunk for
`_gp_disp` is therefore carried; this is recorded here so a future
investigation starts from the probe result rather than the commit alone.

### `.rld_map` was dropped for IRIX (2012)

Commit `e6aea42dfaf13c0e0ca10fa604537a2f43ce9ae9` (Maciej W. Rozycki,
2012-12-03, PR ld/10629) replaced `_bfd_mips_elf_create_dynamic_sections`'s
`.rld_map` creation arm

```c
  if ((IRIX_COMPAT (abfd) == ict_irix5 || IRIX_COMPAT (abfd) == ict_none)
      && !info->shared
      && bfd_get_linker_section (abfd, ".rld_map") == NULL)
```

with `!mips_elf_hash_table (info)->use_rld_obj_head`. IRIX crt1.o defines
`__rld_obj_head`, so for o32 (whose output vector is `elf32-bigmips` and
whose `IRIX_COMPAT` is `ict_irix5`) the section is no longer created at
all, while the 2.20.1 and SGUG 2.23.2 baselines carry it. The
`bfd_get_linker_section`/`bfd_make_section_anyway` form came earlier
(`3d4d4302b9`, "$(bfd_get_linker_section): New function...") and is not
the cause: the section flags already included `SEC_LINKER_CREATED` in
2.20.1, where the orphan script still placed it. `DT_MIPS_RLD_MAP_REL`
(`a5499fa464`, 2015) is unrelated and is kept.

Patch 0003 restores the `ict_irix5` arm for `bfd_link_executable` output
and keeps the modern `!use_rld_obj_head` rule for every other MIPS
target. On this host it restores the `.rld_map` marker section at
`0x10000010` in the o32 output exactly as the baseline has it; the map
slot itself is still `__rld_obj_head` in `.sbss` (baseline `0x10000070`,
candidates `0x10000068`), and both sit inside the RW segment's
zero-filled tail on this host.

### GOT-local classification (SGUG 2.23, onre 2.44)

`sgidevnet/sgug-rse`'s `packages/binutils/binutils2_23.sgifixes.patch`
restricts the local GOT to forced-local and undefined symbols in
`mips_elf_resolve_final_got_entries` and adds a `check_forced` argument
to `mips_elf_local_relocation_p`, used from
`mips_elf_calculate_relocation`'s `local_p`. `onre/binutils-gdb` branch
`binutils-2_44-irix` (commit `4b55be5884a3`) re-derived only the predicate
half as `mips_use_local_got_p` (`mips_is_entry_forced_local`); the
candidate series carries both halves, mapped to 2.47 where
`mips_elf_count_got_symbols` and `mips_elf_calculate_relocation` consume
`mips_use_local_got_p`.

### Other post-SGUG deltas

- `DT_MIPS_RLD_MAP_REL` (`a5499fa464`, "Add support for
  DT_MIPS_RLD_MAP_REL.") is emitted for every executable in 2.47 and is
  kept: IRIX rld ignores the unknown tag, and removing it would touch
  generic MIPS output.
- `.MIPS.abiflags`/`PT_MIPS_ABIFLAGS` (`351cdf24d2`, "[MIPS] Implement
  O32 FPXX, FP64 and FP64A ABI extensions", 2014) is newer than the SGUG
  reference; no hunk is carried yet.
- Modern ld no longer emits the local hidden `.dynsym` entries
  `__TMC_END__`/`__DTOR_END__` (MIPS_SYMTABNO 22 vs 24, MIPS_GOTSYM 0x9
  vs 0xb), an as-yet unidentified generic change; no hunk is carried yet.

These two remain deliberate no-patch verdicts until a candidate's guest
smoke shows one of them matters.

## Candidate series

`patches/binutils-2.47/series` (candidate only; not selected by the build
script yet) applies with `patch -p1` from the binutils-2.47 source root:

| patch | change |
| --- | --- |
| `0001-irix-got-local-restoration.patch` | SGUG's forced-local GOT predicate plus the `check_forced` relocation-time half |
| `0002-irix-gp-global-absolute.patch` | `_gp = ABSOLUTE (ALIGN (16) + 0x7ff0)` (not `HIDDEN`) in the o32 and n32 emulation scripts |
| `0003-irix-rld-map-section.patch` | restore the `ict_irix5` `.rld_map` creation arm removed by `e6aea42dfa` |

Five prefixes were built in scratch for the controlled guest smoke, each
with a generated specs file (the spec routes the driver's `as`/`ld` to the
candidate prefix as described in [toolchain.md](toolchain.md)):

| candidate | patches | prefix | specs |
| --- | --- | --- | --- |
| a | 0001 | `.scratch/binutils-build/prefix-cand-a` | `.scratch/binutils-build/diag/cand-a.specs` |
| b | 0002 | `.scratch/binutils-build/prefix-cand-b` | `.scratch/binutils-build/diag/cand-b.specs` |
| c | 0001+0002 | `.scratch/binutils-build/prefix-cand-c` | `.scratch/binutils-build/diag/cand-c.specs` |
| d | 0001+0003 | `.scratch/binutils-build/prefix-cand-d` | `.scratch/binutils-build/diag/cand-d.specs` |
| e | 0001+0002+0003 | `.scratch/binutils-build/prefix-cand-e` | `.scratch/binutils-build/diag/cand-e.specs` |

Signatures of `oracle/hello.c` linked through each candidate (`_gp`
binding, `.symtab`; GOT tags from `.dynamic`; o32 has MIPS_HIPAGENO, n32
is NEWABI and has none; `.rld_map` address and the RW segment sizes from
the candidate binutils readelf; `MIPS_RLD_MAP` is the map slot):

| candidate | ABI | `_gp` | LOCAL_GOTNO | GOTSYM | HIPAGENO | `.rld_map` | RW fsz/msz | MIPS_RLD_MAP |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| a | o32 | LOCAL section .got | 9 | 0x9 | 7 | absent | 0x68/0xa0 | 0x10000068 |
| a | n32 | LOCAL section .got | 12 | 0x9 | – | absent | 0x14c/0x19c | 0x10010be0 |
| b | o32 | GLOBAL ABS | 13 | 0xd | 11 | absent | 0x68/0xa0 | 0x10000068 |
| b | n32 | GLOBAL ABS | 16 | 0xd | – | absent | 0x14c/0x19c | 0x10010be0 |
| c | o32 | GLOBAL ABS | 9 | 0x9 | 7 | absent | 0x68/0xa0 | 0x10000068 |
| c | n32 | GLOBAL ABS | 12 | 0x9 | – | absent | 0x14c/0x19c | 0x10010be0 |
| d | o32 | LOCAL section .got | 9 | 0x9 | 7 | @0x10000010, size 0 | 0x68/0xa0 | 0x10000068 |
| d | n32 | LOCAL section .got | 12 | 0x9 | – | absent | 0x14c/0x19c | 0x10010be0 |
| e | o32 | GLOBAL ABS | 9 | 0x9 | 7 | @0x10000010, size 0 | 0x68/0xa0 | 0x10000068 |
| e | n32 | GLOBAL ABS | 12 | 0x9 | – | absent | 0x14c/0x19c | 0x10010be0 |
| 2.20.1 baseline | o32 | GLOBAL ABS | 9 | 0xb | 7 | @0x10000010, size 0 | 0x68/0xa0 | 0x10000070 |

Candidate a restores the SGUG GOT counts (9/0xb shape, 22-entry dynsym),
but keeps `_gp` local in `.got`, exactly like onre's SIGBUS build.
Candidate b restores the classic `_gp` symbol but keeps vanilla's GOT
classification. Candidate c combines both. Candidates d and e add the
`.rld_map` restoration to a and c; on this host the map slot sits in the
`.sbss` tail at `0x10000068` (the baseline has `__rld_obj_head` at
`0x10000070`, still inside the same zero-filled region); the guest
decides whether the restored section is what rld needs. All five pass the
guest-free regression.

## Host verification

`scripts/test-binutils-vanilla.sh` remains the committed, guest-free
regression. It checks identity, o32/n32 emission (ELF headers and
relocations) and the o32/n32 dynamic link (IRIX startfiles, interpreter,
libc/libm) against a candidate binutils prefix through a generated specs
file. All five candidates pass it with `--binutils-version 2.47`, and the
script still passes against 2.20.1 with `--binutils-version 2.20.1`.

The prefixes and specs above are scratch artefacts (`.scratch/` is
ignored); the in-repo series is reproducible with the same `configure`
line as the vanilla recipe plus `patch -p1` in series order.

## Guest smoke for the integrator

The current round is cand-d (SGUG GOT + `.rld_map`) and cand-e (SGUG GOT
+ `_gp` + `.rld_map`); cand-a/b/c are retained for regression comparison.
Each run uses the existing 16.2 cross and the candidate's specs file:

```sh
scripts/smoke.sh --prefix .scratch/toolchain-16.2.0/prefix \
	--cflags "-specs=<worktree>/.scratch/binutils-build/diag/cand-e.specs -lm" \
	oracle/hello.c scripts/smoke/hello.expected
scripts/smoke.sh --prefix .scratch/toolchain-16.2.0/prefix --abi n32 \
	--cflags "-specs=<worktree>/.scratch/binutils-build/diag/cand-e.specs -lm" \
	oracle/hello.c scripts/smoke/hello.expected
```

The integrator runs the controlled guest smoke; a candidate must pass o32
and n32 before it is folded in. If a candidate passes, the selector's 2.47
recipe moves from `vanilla` to this series, `scripts/lib/test-build-identity.py`
is updated for the recipe name, `scripts/test-binutils-vanilla.sh` naming
and docs follow the patched 2.47, and the unused candidate patches are
dropped.

## Publication

`irix7/binutils-gdb` receives the surviving minimal series (or the
hunks' no-patch verdicts) as a maintainer push step; this branch does not
push. Provenance to carry across: the pins above, SGUG-RSE
`binutils2_23.sgifixes.patch`, onre `4b55be5884a3`, upstream commits
`9e8082845f85` and `3be08ea4728b`, and this document as the decision
record. No SGI or licence-restricted material is involved; binutils is
GPL.
