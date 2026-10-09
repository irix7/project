# hinv rebuilt with GCC

`hinv` is the first tree command rebuilt with the project's own toolchain:
`scripts/rig/hinv-gcc.sh` cross-compiles `irix/cmd/hinv/hinv.c` against the
tree's headers and the captured sysroot, links it dynamically n32, ships it to
the guest with `iris-ci` and diffs its stdout against the native MIPSpro
reference from issue #7 (ADR-0005). The reference's dry-run and verbose logs
in `$RIG_ORACLE_DIR/hinv-reference/` fix the flags that must be reproduced;
this file records every translation and every divergence.

The GCC build here is carried by flags alone. Source modernisation is not
forbidden; it belongs on the modernised tree branch (ADR-0020).

## Result

| | |
|---|---|
| Acceptance compiler | `mips-sgi-irix6.5-gcc (GCC) 16.2.0` (see the acceptance run below) |
| ABI | ELF N32 MSB mips-3, dynamically linked (`/lib32/libc.so.1`, rpath `/lib32`) |
| Guest stdout | byte-identical to `$RIG_ORACLE_DIR/hinv-reference/hinv.output` (`sha256 060f8641a0768e70c78ae588a3b00e6596707501add0c88a1a61687859439df6`) |
| Guest exit status | 0 |
| Guest `file(1)` | `ELF N32 MSB mips-3 dynamic executable (not stripped) MIPS - version 1`, matching the reference's ABI proof |
| Object and binary | `.scratch/hinv-gcc/<run>/hinv.o`, `hinv` |

## Flag translation

The MIPSpro command comes from `smake.dryrun.overrides.log`; the GCC command
is what `scripts/rig/hinv-gcc.sh` emits (printed by `--print-commands`).
Flags are grouped by the MIPSpro stage they belong to.

### Compile

| MIPSpro | GCC | Disposition |
|---|---|---|
| `-I/tmp/hinv-reference/irix/kern` | `-I$TREE/irix/kern` | kept as a local include; supplies the kernel-only `sys/EVEREST/diagval_strs.i` and the tree's `evdiag.h` |
| `-I/tmp/hinv-reference/irix/usr/include` | `-I$TREE/irix/usr/include` | kept as a local include; supplies `diskinvent.h` (untracked supplement) |
| `-nostdinc` | dropped | `--sysroot` puts the sysroot's `/usr/include` in the equivalent search position while leaving GCC's own include directory available, which the port needs for builtins such as `bcopy`/`bzero` |
| `-I//usr/include` | `--sysroot=$SYSROOT` | the driver maps `/usr/include` under the sysroot; no literal `-I` remains |
| `-mips3` | `-mips3` | kept; ISA selector, same spelling |
| `-n32` | `-mabi=n32` | kept; GCC spelling of the ABI |
| `-O` | `-O` | kept. MIPSpro's driver expands `-O` to the back end's `-O2`, while GCC's `-O` is `-O1`; optimiser intensity is not behavioural, and the output diff confirms it, so the divergence is recorded rather than forced to `-O2` |
| `-MDupdate Makedepend` | `-MD` | optional translation: GCC writes `hinv.d` beside the object instead of updating a shared `Makedepend`; kept so the dependency record is captured |
| `-woff 1685,515,608,658,799,803,852,1048,1233,1499` | dropped | MIPSpro's numbered warning controls have no GCC numbering equivalent. GCC's diagnostics are reviewed instead (see warning triage); none are errors |
| `-c hinv.c` | `-c $SRC` | kept |
| `-o hinv` | `-o $OUT/hinv.o` | kept with a host path |

### Link

| MIPSpro | GCC | Disposition |
|---|---|---|
| `-Wl,-I,/lib32/libc.so.1,-rpath,/lib32` | same spelling | kept literally. MIPSpro's `-I` selects the rld/PT_INTERP and GNU ld's `-I` is also `--dynamic-linker`; both `PT_INTERP=/lib32/libc.so.1` and `RPATH=/lib32` then match the reference |
| `-quickstart_info` | dropped | MIPSpro's quickstart directive; GNU ld has no equivalent, so `MIPS_FLAGS` differs (recorded below) |
| `-nostdlib` | dropped | the GCC IRIX specs add `crt1.o`, `irix-crti.o`, `crtbegin.o`, `crtend.o`, `irix-crtn.o` and `crtn.o` from the sysroot and toolchain |
| `-L//usr/lib32/mips3` | covered by `--sysroot` | dropped; the specs search `$SYSROOT/usr/lib32/mips3` |
| `-L//usr/lib32` | covered by `--sysroot` | dropped; the specs search `$SYSROOT/usr/lib32` |
| `-L//usr/lib32/internal` | dropped | no such directory in the capture and no symbol hinv needs from it |
| `-mips3 -n32` | `-mips3 -mabi=n32` | kept; the driver passes the ISA to `ld` as well |

### GCC-only additions (not translations)

| Flag | Why |
|---|---|
| `--sysroot=$SYSROOT` | the single replacement for `-nostdinc -I//usr/include` and the three `-L` flags |
| `-std=gnu89` | GCC 16 defaults to C23; the 1990s tree code uses implicit `int` and implicit function declarations that the modern default rejects. `gnu89` matches the dialect MIPSpro compiled (`-LANG:=ansi_c` with K&R definitions) and keeps the build a stock one: no source patch is needed |
| `-MD` | dependency capture (see above) |

## Exact commands

Host paths below are for the 15.2 bring-up datapoint
(`--prefix .scratch/toolchain/prefix`); the acceptance run substitutes
`--prefix .scratch/toolchain-16.2/prefix`. Both are emitted verbatim to
`build-commands.txt` in the evidence directory.

```sh
# compile
.scratch/toolchain/prefix/bin/mips-sgi-irix6.5-gcc \
    --sysroot=/mnt/europa/sgi-toolchain-scratch/rig/oracle/sysroot \
    -mips3 -mabi=n32 -O -std=gnu89 -MD \
    -I/home/matt/projects/irix-6.5.7m-src/irix/kern \
    -I/home/matt/projects/irix-6.5.7m-src/irix/usr/include \
    -c /home/matt/projects/irix-6.5.7m-src/irix/cmd/hinv/hinv.c \
    -o .scratch/hinv-gcc/15.2/hinv.o

# link (dynamic n32, MIPSpro rld marker translated literally)
.scratch/toolchain/prefix/bin/mips-sgi-irix6.5-gcc \
    --sysroot=/mnt/europa/sgi-toolchain-scratch/rig/oracle/sysroot \
    -mips3 -mabi=n32 -O \
    -Wl,-I,/lib32/libc.so.1 -Wl,-rpath,/lib32 \
    -o .scratch/hinv-gcc/15.2/hinv .scratch/hinv-gcc/15.2/hinv.o
```

The driver then runs `cc1` and `collect2` over the sysroot's startfiles:
`$SYSROOT/usr/lib32/mips3/crt1.o`, the toolchain's
`n32/irix-crti.o`, `n32/crtbegin.o`, `-lgcc -lm -lc -lgcc -lm` (the IRIX GCC
port's default math pairing), `n32/crtend.o`, `n32/irix-crtn.o` and
`$SYSROOT/usr/lib32/mips3/crtn.o` (visible in the link with `-v`).

## Bring-up datapoint: baseline 15.2

Before the 16.2 acceptance run, the script was exercised against the frozen
15.2 baseline cross to prove the whole loop with a known-good compiler:

```sh
scripts/rig/hinv-gcc.sh --prefix .scratch/toolchain/prefix \
    --out .scratch/hinv-gcc/15.2 --tree /home/matt/projects/irix-6.5.7m-src
```

- compile: exit 0, six `-Wbuiltin-declaration-mismatch` warnings for
  `bcopy`/`bzero` (below)
- link: exit 0, `PT_INTERP` present
- guest run: exit 0, stdout sha256
  `060f8641a0768e70c78ae588a3b00e6596707501add0c88a1a61687859439df6`,
  byte-identical to `hinv.output`
- guest `file(1)`: `hinv: ELF N32 MSB mips-3 dynamic executable (not
  stripped) MIPS - version 1`

The guest command is shipped as a small runner script (`guest-run.sh` in the
evidence directory) rather than passed inline: `docs/reference-hinv.md`
records that the guest's serial input silently wedges on long command lines,
and a `sh -c '...'` one-liner carrying two redirects, the status echo and the
`file` call is long enough to hit it. `run "sh $guest_bin.run"` is short and
returns immediately; the reference script uses the same short-command rule.

## 16.2 acceptance run

Issue #6's forward-port announced itself in `.scratch/toolchain-16.2/READY`:

```
prefix: /home/matt/projects/sgi-toolchain-new/.scratch/toolchain-16.2/prefix
gcc: mips-sgi-irix6.5-gcc (GCC) 16.2.0
status: GCC 16.2.0 release series built; verify o32/n32/n64 passed; dynamic smoke o32 and n32 passed on the guest
```

The acceptance run is the script invoked with that prefix and the tree's
commit (`822f4fcb99524a2fa03747acc4c5946eeb63ea3c`):

```sh
scripts/rig/hinv-gcc.sh --prefix .scratch/toolchain-16.2/prefix \
    --out .scratch/hinv-gcc/16.2 --tree /home/matt/projects/irix-6.5.7m-src
```

| Check | Result |
|---|---|
| compiler | `mips-sgi-irix6.5-gcc (GCC) 16.2.0` (`gcc-version.txt`) |
| sysroot | `/mnt/europa/sgi-toolchain-scratch/rig/oracle/sysroot` (the cross's configured capture) |
| compile | exit 0; diagnostics byte-identical to the 15.2 datapoint |
| link | exit 0; dynamic n32 |
| guest run | exit 0; stderr empty |
| guest `file(1)` | `ELF N32 MSB mips-3 dynamic executable (not stripped) MIPS - version 1` |
| stdout | sha256 `060f8641a0768e70c78ae588a3b00e6596707501add0c88a1a61687859439df6`, byte-identical to `hinv.output` |
| PT_INTERP / RPATH | `/lib32/libc.so.1` / `/lib32`, matching the reference |

The 16.2 build differs from the 15.2 datapoint only in code generation:
header flags, dynamic entries, `PT_INTERP`, `RPATH`, section names and the
translated command lines are identical, while `.text` is 0x6d40 vs 0x6d90
bytes and the binary 70,403 vs 70,571 bytes. The compiler upgrade changed
code, not linkage or ABI.

Acceptance criteria:

- [x] GCC 16.2 builds `hinv` from the tree
- [x] the binary runs on the guest and matches the native reference output
- [x] the MIPSpro-to-GCC flag translation is recorded (table above)
- [x] divergences are triaged against the oracle and recorded (below)

## Divergence triage

Every difference against the native reference is attributed to the toolchain,
not the build recipe, and none is behavioural.

### Output

Byte-identical on the guest. `hinv` reads the emulated hardware and the
inventory interface, not the compiler, so this is the strongest proof that
the translation did not change behaviour.

### Compiler diagnostics

- MIPSpro's verbose build emits `cc-1174` (unused variable) and `cc-1552`
  (set but unused) warnings, and an `-OPT:Olimit` overflow on `display_item`;
  the build completes. None is a correctness signal.
- GCC emits six `-Wbuiltin-declaration-mismatch` warnings: `bcopy` and
  `bzero` are called without a prototype because `hinv.c` does not include
  `<strings.h>`/`<bstring.h>`. GCC recognises the names as builtins and warns
  that the implicit declaration disagrees with the builtin. This is the same
  class of warning MIPSpro reported for other symbols; it is not a build
  failure and the guest output confirms the calls behave. No suppression flag
  is added because the diagnostics are wanted in the record.
- No GCC error occurs; no tree source change was needed (ADR-0005).

### ELF shape (`hinv`)

`hinv.elf-shape.diff` records `readelf -h -l -d -S` of the GCC binary against
the same dump of the MIPSpro binary. The differences are the expected
toolchain and code-generation divergence:

- code size, entry point, section sizes and the first `LOAD` alignment differ
  (GCC's compiled body is smaller: `.text` is 0x6d40 for 16.2 and 0x6d90 for
  15.2, against MIPSpro's 0x8cc0);
- header flags gain `noreorder` and `pic` next to MIPSpro's `cpic`; `abi2` and
  `mips3` agree, so the ABI is identical;
- GCC adds its own scaffolding: `.gcc_init`, `.gcc_fini`, `.eh_frame`,
  `.ctors`, `.dtors`, `.data.rel.ro`, `.gnu.attributes` and its DWARF
  sections. MIPSpro emits SGI-specific sections GCC does not:
  `.liblist`, `.MIPS.symlib`, `.msym`, `.MIPS.events.*`, `.MIPS.content.*`,
  plus `.sdata`/`.sbss`/`.lit8`;
- `.interp` and the interpreter string agree (`/lib32/libc.so.1`); `RPATH`
  agrees (`/lib32`);
- dynamic entries differ where the toolchains differ: GCC records
  `NEEDED libc.so.1` and an extra driver-default `NEEDED libm.so` (the IRIX
  GCC specification links `-lm` around `-lc` whether or not it is used),
  while MIPSpro records only `libc.so.1`; `MIPS_FLAGS` is `NOTPOT` where
  MIPSpro's `-quickstart_info` sets
  `QUICKSTART SGI_ONLY REQUICKSTART NO_UNRES_UNDEF RLD_ORDER_SAFE`, and GCC
  has `INIT`/`FINI` pointing at `.gcc_init`/`.gcc_fini` where MIPSpro has
  `0x0`.

Neither `NEEDED libm.so` (the guest's `/lib32/libm.so.1` exists; `hinv` runs)
nor `MIPS_FLAGS` affects execution: both binaries load, link and produce the
same inventory. They are recorded, not forced away, because suppressing them
would mean overriding the driver's stock specs rather than translating the
tree's flags.

### Object (`hinv.o`)

`reference.hinv.o.readelf.txt` and `hinv.o.readelf.txt` accompany the diff.
The object is relocatable n32 with the same machine flags; section names,
relocation styles and symbol counts differ because the compilers and their
debug formats differ. This is compile-side codegen evidence for the same
exercise, not an acceptance gate.

## Warning triage summary

| Source | Warning | Disposition |
|---|---|---|
| GCC | 6 x `bcopy`/`bzero` implicit builtin declaration | accepted; matches MIPSpro's class of warnings; output proves the calls |
| MIPSpro | unused/set-but-unused variables (`cc-1174`, `cc-1552`), `-OPT:Olimit` on `display_item` | reference noise; GCC does not report the same set (no `-Wall`), none behavioural |
| MIPSpro | licence banner from the patched driver | known reference quirk, not an error |

## Rerun

```sh
# host-only flag-translation test
python3 scripts/rig/test-hinv-gcc.py

# build and check the ELF shape, no guest
scripts/rig/hinv-gcc.sh --tree /home/matt/projects/irix-6.5.7m-src --no-run

# full run: build, link, ship, run, diff
scripts/rig/hinv-gcc.sh --tree /home/matt/projects/irix-6.5.7m-src
```

Options mirror `scripts/smoke.sh`: `--prefix`, `--sysroot`, `--tree`,
`--out`, `--timeout`, `--no-run`, `--print-commands`. The guest transaction
takes the rig's shared guest lock, so it serialises with every other client.
Evidence stays under `.scratch/hinv-gcc/`; nothing captured from the guest or
built from the tree is committed (ADR-0001).
