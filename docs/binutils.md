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
| cand-d (0001+0003) | o32 SIGBUS (138); n32 passes |
| cand-e (0001+0002+0003) | o32 SIGBUS (138); n32 passes |
| cand-g (0001+0002+0003+0004) | o32 SIGBUS (138); n32 passes |

The first o32 root-cause lead was `.rld_map`: the working 2.20.1 output
carries the section and the map slot sits inside the RW segment's
zero-filled tail, while the 2.47 variants had no `.rld_map`,
`DT_MIPS_RLD_MAP` named a slot in the `.sbss` tail, and (on the
guest-tested binaries) the RW segment's `p_memsz` equalled `p_filesz`.
Patch 0003 restored the section, but cand-d/e still die with SIGBUS on
o32, so `.rld_map` is necessary but not sufficient.

The second lead was `.compact_rel`, but the guest disproved it as the
crash: the working 2.20.1 o32 table's header has `num=0` and an all-zero
body (the 0x954 bytes are over-allocation), and cand-g carries the same
shape yet still fails o32. Patch 0004's restored count is therefore
inert for the loader, 0004 is dropped from the series, and the native
MIPSpro `num=2` case is a MIPSpro-only path. The later cand-h core decode
identified the actual fault as a misaligned `__istart`; see "Malformed
o32 crt1 alignment" below.

Cand-h (0001 + 0003 + 0005) still fails o32 with SIGBUS 138 and passes
n32. It is retained as the pre-alignment comparison point, not as a
candidate for guest testing.

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
(`a5499fa464`, 2015) is a separate compatibility issue, suppressed for
SGI output by patch 0005.

Patch 0003 restores the `ict_irix5` arm for `bfd_link_executable` output
and keeps the modern `!use_rld_obj_head` rule for every other MIPS
target. On this host it restores the `.rld_map` marker section at
`0x10000010` in the o32 output exactly as the baseline has it; the map
slot itself is still `__rld_obj_head` in `.sbss` (baseline `0x10000070`,
candidates `0x10000068`), and both sit inside the RW segment's
zero-filled tail on this host.

### `DT_MIPS_RLD_MAP_REL` was added after IRIX (2015)

Commit `a5499fa4649e4325cf46edfff2f24dae2fe2afef` (Matthew Fortune,
2015-06-11) added `DT_MIPS_RLD_MAP_REL` and emits it for every
executable; the decompiled native MIPSpro linker knows
`DT_MIPS_RLD_MAP` (0x70000016), `DT_MIPS_COMPACT_SIZE` (0x7000002f) and
`DT_MIPS_GP_VALUE` (0x70000030) but has no `DT_MIPS_RLD_MAP_REL`
(0x70000035). Every 2.47 o32 hello therefore carries one extra
`.dynamic` entry (21 vs the working 2.20.1 link's 20) and a tag the
IRIX 6.5.7 o32 loader never knew. Patch 0005 gates the tag on
`!SGI_COMPAT (info->output_bfd)`, so SGI output keeps only
`DT_MIPS_RLD_MAP` with the 2.20.1 dynamic layout; generic MIPS/Linux is
unchanged.

### Malformed o32 crt1 alignment

The guest core resolves the remaining o32 SIGBUS precisely: CAUSE
ExcCode 4, EPC = BADVADDR = `0x0040051e`; GOT slot 18 contains that same
address, the value of `__istart`. The captured o32 `crt1.o` defines
`__istart` at offset `0xc` into `.init`, whose ELF `sh_addralign` is 27
(`0x1b`), a malformed non-power-of-two value. cand-h places `.init` at
`0x400512` with alignment 1, leaving `__istart=0x40051e` (2 mod 4), so
the loader's `jalr` raises the address error. The working 2.20.1 output
places `.init` at `0x400560`, alignment 16, and `__istart=0x40056c`.

Two upstream changes explain the history:

- `9e6619e285873fe1cb002da4bb7749be40a5627c` (Alan Modra, 2011-04-20)
  changed `bfd_log2` from rounding down to rounding up. On the 2.20.1
  path, raw 27 gave alignment power 4 (16); after this change, 27 gives
  power 5 (32). The SGUG 2.23.2 reference contains the round-up change.
- `1f9b1a84350d3755ce8620900a35c3d1997535e6` (Alan Modra, 2022-02-15,
  "What to do when sh_addralign isn't a power of two") changed
  `_bfd_elf_make_section_from_shdr` to use
  `bfd_log2 (sh_addralign & -sh_addralign)`. This safely chooses the
  greatest power-of-two divisor for invalid ELF values, but turns 27 into
  alignment power 0 (1) in 2.47. This is the change that makes the input
  code under-aligned; 2.20.1's 16 and 2.23.2's 32 were both safe.

An instrumented cand-j link confirms the BFD values on the actual input:
for `crt1.o` `.init`, raw `sh_addralign=27`, generic `alignment_power=0`,
then the SGI compatibility normaliser sets `alignment_power=4`. The
resulting output has `.init` at `0x400560`, alignment 16, and
`__istart=0x40056c`. The MIPS `section_from_shdr` backend callback is
bypassed for ordinary `SHT_PROGBITS`; the correction therefore runs in
the o32 and n32 `object_p` callbacks, after all sections have been
created, and only under `SGI_COMPAT`. It clamps malformed non-power-of-two
alignments to the largest power of two not exceeding the input. Valid
alignments and generic MIPS/Linux inputs are unchanged. Patch 0006 records
this fix and both upstream commits' provenance.

### Compact relocation accounting stopped (2020)

Upstream commit `c4b126b87a6cd842e567136b07ac1adca98c660f` (H.J. Lu,
2020-06-04, PR ld/26080) made `_bfd_elf_link_iterate_on_relocs` skip
sections without `SEC_ALLOC`, so `_bfd_mips_elf_check_relocs` no longer
sees debug-section relocations. For IRIX that silently removed the
`.compact_rel` accounting it had carried since the 2.20.1 era: the count
lived in the same switch as the GOT bookkeeping and included the
`R_MIPS_32` relocations in `.debug_*`.

Instrumenting a 2.20.1 ld and a 2.47 ld on the same o32 hello link (same
driver and inputs, `-specs` selecting each tool) settles it:

- 2.20.1: `check_relocs` is called for the alloc sections and for
  `.debug_line`, `.debug_info`, `.debug_aranges`, `.debug_ranges`,
  `.debug_frame` and `.debug_loc`; 197 of the relocations it walks are
  primary `R_MIPS_32` (type 2), and `compact_rel_size` reaches 2364
  (0x93c). The final table is 0x954 with `num = 0` and a zero body.
- 2.47: only `.text`, `.gcc_init` and `.gcc_fini` reach `check_relocs`;
  the primary types are LO16/GOT16/HI16/CALL16/JALR/PCREL etc., no
  accounting type is seen and `compact_rel_size` stays 0, leaving the
  0x18-byte header. Swapping in the 2.20.1 assembler changes nothing:
  the filter, not gas, drops the relocations.

A scratch patch restored the count for `SGI_COMPAT` output by walking the
non-alloc sections of each input with the pre-2020 filter (cand-f/g), and
it did reproduce the 0x954 table shape. The guest then showed it inert:
cand-g still fails o32, and the working 2.20.1 table has `num = 0` with a
zero body, so the loader never reads those reserved bytes. The patch
(0004) has been dropped from the series and deleted; this archaeology is
kept because it explains the section-size difference, and because the
native MIPSpro `num = 2` case is a MIPSpro-only path. No
`DT_MIPS_COMPACT_SIZE` hunk is carried either: the working 2.20.1 GNU link
does not emit it.

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

- `DT_MIPS_RLD_MAP_REL` (`a5499fa464`) is suppressed for `SGI_COMPAT`
  output by patch 0005 (see above); generic MIPS/Linux keeps it.
- `.MIPS.abiflags`/`PT_MIPS_ABIFLAGS` (`351cdf24d2`, "[MIPS] Implement
  O32 FPXX, FP64 and FP64A ABI extensions", 2014) is newer than the SGUG
  reference; no hunk is carried yet.
- Modern ld no longer emits the local hidden `.dynsym` entries
  `__TMC_END__`/`__DTOR_END__` (MIPS_SYMTABNO 22 vs 24, MIPS_GOTSYM 0x9
  vs 0xb). A probe in the instrumented 2.47 ld shows both symbols reach
  `mips_elf_sort_hash_table_f` with `dynindx = -1`, `forced_local = 1`
  and `GGA_NONE`, so it returns before placing them; the 2018 commit
  `3be08ea4728b` is *not* the cause (its hunks are `_gp_disp`-specific),
  and the 2017 gABI sort commit `e17b0c351f` already handles forced-local
  placement only for symbols that have an index. The open lead for
  cand-i is therefore the earlier stage that stopped recording hidden
  defined symbols as dynamic; no hunk is carried yet.

MIPS.abiflags and the local-dynsym entries remain deliberate no-patch
verdicts until a candidate's guest smoke shows one of them matters.

## Candidate series

`patches/binutils-2.47/series` (candidate only; not selected by the build
script yet) applies with `patch -p1` from the binutils-2.47 source root:

| patch | change |
| --- | --- |
| `0001-irix-got-local-restoration.patch` | SGUG's forced-local GOT predicate plus the `check_forced` relocation-time half |
| `0003-irix-rld-map-section.patch` | restore the `ict_irix5` `.rld_map` creation arm removed by `e6aea42dfa` |
| `0005-irix-no-rld-map-rel.patch` | suppress `DT_MIPS_RLD_MAP_REL` for `SGI_COMPAT` output (`a5499fa464`) |
| `0006-irix-crt1-section-alignment.patch` | restore the 2.20.1 floor normalisation for malformed SGI input alignment; upstream history `9e6619e285` and `1f9b1a8435` |

Dropped after guest evidence and removed from the tree (git history keeps
them): `0002` (`_gp` GLOBAL ABS; cand-b failed n32 and it did not fix
o32) and `0004` (compact count; cand-g still failed o32 and the table is
inert with `num = 0`). Every prefix was built in scratch with a generated
specs file, the spec routing the driver's `as`/`ld` to the candidate
prefix as described in [toolchain.md](toolchain.md). The current guest-smoke
round is **cand-j** (`0003+0006`) followed by **cand-k**
(`0001+0003+0005+0006`); cand-a..h remain for comparison.

Signatures of `oracle/hello.c` (`dyn` is the `.dynamic` entry count,
`RLD_MAP/REL` the presence of `DT_MIPS_RLD_MAP`/`DT_MIPS_RLD_MAP_REL`,
`TMC` the `__TMC_END__`/`__DTOR_END__` count in `.dynsym`,
`.compact_rel` its size and header `num`, `_gp` from `.symtab`):

| variant | ABI | dyn | RLD_MAP/REL | TMC | SYMTABNO | LOCAL_GOTNO | GOTSYM | `.compact_rel` | `.rld_map` | `_gp` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2.20.1 baseline | o32 | 20 | yes/no | 2 | 24 | 9 | 0xb | 0x954, num=0 | present | GLOBAL ABS |
| 2.20.1 baseline | n32 | 20 | yes/no | 2 | 24 | 12 | 0xb | absent | absent | GLOBAL ABS |
| cand-g | o32 | 21 | yes/yes | 0 | 22 | 9 | 0x9 | 0x954, num=0 | present | GLOBAL ABS |
| cand-g | n32 | 21 | yes/yes | 0 | 22 | 12 | 0x9 | absent | absent | GLOBAL ABS |
| cand-h | o32 | 20 | yes/no | 0 | 22 | 9 | 0x9 | 0x18, num=0 | present | LOCAL .got |
| cand-h | n32 | 20 | yes/no | 0 | 22 | 12 | 0x9 | absent | absent | LOCAL .got |

cand-h returns the `.dynamic` layout to the baseline (`DT_MIPS_RLD_MAP`
only, 20 entries) on both ABIs. The remaining o32 delta is the two
missing LOCAL HIDDEN `.dynsym` entries, which shifts SYMTABNO 24 to 22
and GOTSYM 0xb to 0x9; the probe above shows 2.47 never gives those
symbols an index. `_gp` is left LOCAL in `.got` (0002 not carried). This
table is historical; the current candidates' complete start/alignment and
dynamic signatures are below.

The guest-free candidate signatures are:

| candidate | ABI | `.init` address / alignment | `__istart` | dyn | `MIPS_LOCAL_GOTNO/GOTSYM/SYMTABNO/HIPAGENO` | `RLD_MAP/REL` | `.rld_map` section | `.compact_rel` |
| --- | --- | --- | ---: | ---: | --- | --- | --- | --- |
| cand-j (`0003+0006`) | o32 | `0x00400560` / 16 | `0x0040056c` | 21 | `13 / 0xd / 22 / 11` | yes/yes | present at `0x10000010` | 0x18; id1=1, num=0, id2=2, offset=0x1244, reserved=0 |
| cand-j (`0003+0006`) | n32 | `0x10000574` / 4 | `0x10000580` | 21 | `16 / 0xd / 22 / n/a` | yes/yes | absent | absent |
| cand-k (`0001+0003+0005+0006`) | o32 | `0x00400550` / 16 | `0x0040055c` | 20 | `9 / 0x9 / 22 / 7` | yes/no | present at `0x10000010` | 0x18; id1=1, num=0, id2=2, offset=0x1244, reserved=0 |
| cand-k (`0001+0003+0005+0006`) | n32 | `0x1000056c` / 4 | `0x10000578` | 20 | `12 / 0x9 / 22 / n/a` | yes/no | absent | absent |

`DT_MIPS_RLD_MAP` is present in all four images (o32 slot `0x10000068`,
n32 slot `0x10010be0`). The n32 images have no `.rld_map` marker section;
the o32 marker is zero-sized at `0x10000010`. Both o32 compact headers are
24 bytes with `num=0` and six words `1, 0, 2, 0x1244, 0, 0`; neither n32
image has `.compact_rel`. cand-j intentionally retains
`DT_MIPS_RLD_MAP_REL`; cand-k suppresses it with patch 0005.

Candidate prefixes and specs for guest smoke:

| candidate | prefix | specs |
| --- | --- | --- |
| cand-j | `.scratch/binutils-build/prefix-cand-j` | `.scratch/binutils-build/diag/cand-j.specs` |
| cand-k | `.scratch/binutils-build/prefix-cand-k` | `.scratch/binutils-build/diag/cand-k.specs` |

## Host verification

`scripts/test-binutils-vanilla.sh` remains the committed, guest-free
regression. It checks identity, o32/n32 emission (ELF headers and
relocations) and the o32/n32 dynamic link (IRIX startfiles, interpreter,
libc/libm) against a candidate binutils prefix through a generated specs
file. The updated `--check-irix-crt1-alignment` option additionally requires
o32 `.init` alignment of at least 16, n32 alignment of at least 4, aligned
section addresses, and 4-byte-aligned `__istart`. Both cand-j and cand-k
pass all host checks with `--binutils-version 2.47`; guest validation is
pending. The script also retains its vanilla and 2.20.1 modes.

The prefixes and specs above are scratch artefacts (`.scratch/` is
ignored); the in-repo series is reproducible with the same `configure`
line as the vanilla recipe plus `patch -p1` in series order.

## Guest smoke for the integrator

Run cand-j first, then cand-k, for o32 and n32. These are guest commands
for the integrator; no guest command has been run in this work session.
Each run uses the existing 16.2 cross and candidate specs:

```sh
scripts/smoke.sh --prefix .scratch/toolchain-16.2/prefix \
	--cflags "-specs=<worktree>/.scratch/binutils-build/diag/cand-j.specs -lm" \
	oracle/hello.c scripts/smoke/hello.expected
scripts/smoke.sh --prefix .scratch/toolchain-16.2/prefix --abi n32 \
	--cflags "-specs=<worktree>/.scratch/binutils-build/diag/cand-j.specs -lm" \
	oracle/hello.c scripts/smoke/hello.expected
scripts/smoke.sh --prefix .scratch/toolchain-16.2/prefix \
	--cflags "-specs=<worktree>/.scratch/binutils-build/diag/cand-k.specs -lm" \
	oracle/hello.c scripts/smoke/hello.expected
scripts/smoke.sh --prefix .scratch/toolchain-16.2/prefix --abi n32 \
	--cflags "-specs=<worktree>/.scratch/binutils-build/diag/cand-k.specs -lm" \
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
`binutils2_23.sgifixes.patch`, onre `4b55be5884a3`, the carried upstream
commits `9e8082845f85`, `e6aea42dfa`, `a5499fa464`, `9e6619e285` and
`1f9b1a8435`, the dropped-patch
archaeology (`3be08ea4728b`, `c4b126b87a`, `e17b0c351f`), and this
document as the decision record. No SGI or licence-restricted material is
involved; binutils is GPL.
