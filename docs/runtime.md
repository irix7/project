# Runtime: libc and libm rebuilt from the tree

`scripts/runtime/rebuild-libc.py` rebuilds the tree's o32 nonshared C library
and maths library with the GCC 16.2 cross, links the smoke program statically
against them using the tree's own startfiles, runs it on the guest and diffs
its stdout against the recorded oracle output. This is the deferred static
proof of ADR-0006 and the runtime deliverable of issue #9; ADR-0007 records
the static-first decision and why the rebuilt dynamic path is deferred.

The tree is stock (ADR-0005): every MIPSpro-only construct is translated in
the build's own copies of the sources (`scripts/runtime/compat.py`), never in
the tree. The build runs inside the flake devshell because host `cc` and
`m4` reproduce the tree's generated sources.

## Result

| | |
|---|---|
| Compiler | `mips-sgi-irix6.5-gcc (GCC) 16.2.0` |
| Library variant | `libc_32_M2_ns.a` — o32, mips2, nonshared (the released `32_M2` style) |
| Sources selected | 743 C + 258 asm from the tree's own `SUBDIRS` and leaf makefiles |
| Compiled | 960 objects; 40 quad-precision sources excluded (o32 has no 128-bit long double); 1 iconv object linked from its two halves |
| `libc.a` | 959 members |
| `libm.a` | 59 members (the tree's `libc/src/math` leaf, minus the quad set) |
| Static hello | `ELF 32-bit MSB executable, MIPS, flags 0x20000105 (noreorder, cpic, 32bitmode, mips3)`, 248,265 bytes |
| Link proof | no `PT_INTERP`, no `NEEDED` — nothing from the SGI runtime |
| Guest run | exit 0; stdout byte-identical to `scripts/smoke/hello.expected` (sha256 `6fc912fba05a618f3a3e1b65c4309acb60a67427615b54f2840c6b9f71dfd39f`) |

The guest's stdout is the recorded MIPSpro oracle output, so the static
runtime prints exactly what the native library did for the same program.

## One command

```sh
nix develop --command python3 scripts/runtime/rebuild-libc.py \
	--tree /home/matt/projects/irix-6.5.7m-src
```

`--no-run` stops after the link and the readelf proof; `--jobs N` bounds the
compile; `--prefix`, `--sysroot`, `--out` and `--timeout` mirror the other
harnesses. Every artefact lands under `.scratch/runtime/` (or `--out`); the
generated sources, objects, archives, logs and the guest's output stay there
and are never committed (ADR-0001). The guest phase is smoke.sh's fail-closed
transaction (`scripts/lib/guest-txn.sh`, issue #5): it takes the rig's shared
guest lock, checks every transport stage, clears host result files before
retrieval and requires a numeric status, so it serialises with the reference
builds and cannot accept stale evidence.

## Why this source set

The tree enumerates its library contents in smake leaf makefiles with `#if`
conditionals over the library and object style. `scripts/runtime/smake.py`
evaluates that slice of smake and `rebuild-libc.py` compiles exactly the
selection `libc_32_M2_ns.a` names, in `SUBDIRS` order, so the archive carries
the same objects MIPSpro built. The selection is release data; it is not
hand-maintained in the repository.

o32 is the Indy's native environment (ADR-0003) and the variant whose 64 KB
GOT boundary the issue calls out. The assembler runs at mips3 while the C
sources stay mips2: the tree's maths assembly uses 64-bit floating-point
instructions the mips2 assembler rejects, and the R4400 executes them. This
is the only ISA divergence, and it does not change the ABI.

### Generated sources

Four C sources and one header are build products, not files in the upload.
The driver reproduces the tree's generation rules with host tools in
`.scratch/runtime/gen/`:

- `computed_include/mbwcoffs.c`, `matrix.c`, `make_table.c` and
  `gen/mkerrlist.c` are compiled with host `cc` (the original build used
  `HOST_CC`), `-Doserror()=errno` bridging the one IRIX-only helper;
- `mbwcoffs` and `matrix` emit `mbwc_wrap.h` and `iconv_top_include.m4`;
- `m4 -B65536` expands `iconv_top_src.m4` and `stdlib_top_src.m4`, whose
  diverts write `iconv.syms`, `stdlib.syms` and the `.dcls.h` files;
- `make_table` turns `symtab.syms` into `symtab.h`; `mkerrlist` turns
  `gen/errlist` into `errlst.c` and `new_list.c`.

The iconv wrap object is the tree's own recipe: `ld -r` of `mbwc_wrap.o` and
`stdlib_conv.o`, with the two halves left out of the archive so its member
list matches the release.

## Flag translation

The tree's build variables come from the leaf makefiles through the smake
evaluator (`LCDEFS`, `LCINCS`, `SUBDIR_*`); the rest is the `32_M2` style
composed by `commondefs`/`releasedefs`. Translation:

| IRIX build | Rebuild | Why |
|---|---|---|
| `-mips2 -o32` (CSTYLE_32_M2) | `-mabi=32 -mips2` | same ABI and ISA; GCC spelling |
| MIPSpro default small data | `-G 0` | one GP for all of libc overflows the o32 64 KB small-data/GOT boundary; with `-G 0` the library objects emit no `.sdata`/`.sbss` (the startfile keeps its few `.sbss` words) |
| `-xgot` on `crt1.o` | `-mxgot` | the tree's startfile asks for the large GOT; GNU as spelling |
| `-nostdinc -I//usr/include` | `-nostdinc -isystem <gcc> -isystem <tree>/irix/usr/include -isystem <sysroot>/usr/include` | GCC's own headers stay in front (SGI's `stdarg.h` breaks GCC); the tree's supplement covers what the dev install lacks; the captured sysroot is the fallback |
| `-I$(TOP)inc` | `-I<tree>/irix/lib/libc/inc` | internal libc headers |
| `$(ROOT)/usr/include/rpcsvc` etc. | mapped to the tree, then the sysroot | absolute IRIX paths in leaf makefiles |
| `-D_SGI_MP_SOURCE -D_LIBC_NONSHARED -DNDEBUG -D_SGI_COMPILING_LIBC -U__MATH_HAS_NO_SIDE_EFFECTS -U__INLINE_INTRINSICS` | kept | version and library defines |
| `-D_SYSTYPE_SVR4` | added | MIPSpro defined it; the installed headers branch on the single-underscore spelling for SVR4 layouts |
| `-D__return_address=__builtin_return_address(0)` | added | MIPSpro builtin used by `aio.c` |
| `-fullwarn -woff ... -OPT:...` | dropped | no GCC equivalent; diagnostics are not the gate |
| MIPSpro `as` with its own cpp | GCC cpp `-traditional-cpp`, then GNU as | see the construct table |
| `$(GNUM4)` and `HOST_CC` generation | host `m4`, host `cc` | same generators, now on the host |

Assembly is preprocessed first, translated, then assembled without cpp so a
second macro pass cannot undo the translation.

## MIPSpro-only constructs translated

`scripts/runtime/compat.py` carries these, each unit-tested against synthetic
snippets (no tree text in the repository, ADR-0001):

| Construct | Why GCC rejects it | Translation |
|---|---|---|
| `#pragma weak A = B` | GNU cpp expands macros inside the pragma, so `tcgetattr = _tcgetattr` becomes a self-alias and GCC sees a redefinition | `__asm__(".weak A\n.set A, B");` at the pragma's position |
| `.weakext A, B` where the file also defines `A` | GNU as reads the two-operand form as defining `A`, then the local definition is a redefinition | drop the alias operand; the local definition wins |
| `cvt.d.w $fN`, `cvt.s.d $fN`, ... | MIPSpro's one-register shorthand for a same-register conversion | both operands spelled out |
| `li t4, "0"` | double-quoted character constant | `'0'` |
| `.text .init` / `.text .fini` | MIPSpro subsection syntax | `.section .init,"ax",@progbits` and the `fini` twin |
| `SYS_ xpg4_recvmsg` | GNU traditional cpp's nested-macro paste drops the space for this one call | its value from `sys.s`, `1227` |

## Release errata

Two defects in the uploaded tree stop any compiler and are corrected in the
build's copy, not worked around silently:

- `sys/mq_open.c` is a K&R definition of a function whose prototype is
  variadic; GCC rejects an identifier-list definition against a `...`
  prototype. The build renames the definition to `__mq_open_impl` and emits
  `.globl _mq_open; .set _mq_open, __mq_open_impl`, preserving the symbol.
- `locale/sgi_ffmtmsg.c` leaves a call statement without its terminating
  semicolon; the build's syntax-based repair appends it. The matcher carries
  no source text (ADR-0001).

The publication inventory behind these repairs and the current-tree
attestation are recorded in `docs/publication.md`.

Two generated message headers are absent from the upload and the dev
install: `scripts/runtime/include/msgs/uxsgicore.h` and `msgs/uxlibc.h`
re-supply the `gettxt` message identifiers the sources reference. They carry
reconstruction catalogue IDs only; every caller passes its own default text,
so behaviour without a catalogue is unchanged. The kernel-only
`sys/t6api_private.h` is staged from `irix/kern/bsd/sesmgr/` under the
`sys/` name the source expects.

## The static-first boundary

- **TLS off.** The IRIX target carries no thread-local storage, and the
  rebuilt nonshared archive is not a TLS runtime; the toolchain's TLS probe
  stays disabled (the port's TLS hunk only guards the assembler check).
- **LTO off.** No `-flto` in the build or the cross's specs; the 1990s
  sources mix K&R, assembler and compiler-specific pragmas that bytecode
  cannot carry.
- **64 KB o32 GOT.** o32 addresses small data through a single `$gp`; the
  whole libc's `.sdata`/`.sbss` overflows 64 KB at link time. The rebuild
  compiles `-G 0` and gives `crt1.o` `-mxgot`, the GNU spelling of the
  tree's `-xgot`. Larger GOTs are the o32 boundary ADR-0003 records.
- **rld symbols are stubbed.** `libcthread.o`'s `locate_dso()` asks the
  runtime linker for `__elf_header`, `__program_header_table` and
  `__dso_displacement`. A static process has no rld, so
  `scripts/runtime/static-rld-stub.c` provides zeroed storage: the walk
  reads zero program headers and returns `ESRCH`, the correct "no DSOs"
  answer. The lock and thread entry points in the archive still resolve.
- **o32 only.** The toolchain carries n32 and n64, but this proof is the
  Indy's native ABI; n64 is out of scope (ADR-0003 scope note). n32 runtime
  variants are later work.
- **The quad-precision set is excluded.** The maths leaf's own
  `QUAD_WORD_CFILES`/`QUAD_WORD_ASFILES` (38 C and 2 asm sources) operate on
  `_ldblval`, the 128-bit long double type that the tree's `fpparts.h`
  defines only for n32/n64, and the released o32 `libc`/`libm` export none of
  their symbols. The driver drops exactly the members those variables name,
  so the archive has no dangling quad references; everything else the o32
  maths carries (sqrt, conversions, arithmetic) is present.

## libm

The 6.5.7m upload contains no standalone maths library source: the shared
`/usr/lib32/mips3/libm.so` and `/usr/lib/libm.a` were built from a source set
that is not in the tree. The tree's maths sources live in `libc/src/math`
and are what `libm.a` archives here (59 members), so a static `-lm` resolves
from rebuilt code. The captured SGI `libm.so` remains for dynamic links until
the shared runtime is rebuilt; the only `libm.a` in the capture is the n64
copy under `/usr/lib64/abi`.

## Dynamic linking against the rebuilt runtime: deferred

The dynamic model against the captured SGI runtime is already proven
(ADR-0006, issues #5, #6 and #8): the cross links the sysroot's `crt1.o`,
`libc.so.1` and `libm.so`, and `smoke.sh` executes the result. Doing the same
against the *rebuilt* runtime is a different problem and is explicitly
deferred:

- IRIX's `rld` (the runtime linker mapped as `PT_INTERP`) is shipped
  binary-only; the tree's `libc/src/rld` sources are the interface around it,
  not the loader. Rebuilding the loader is its own milestone (issue #15's
  neighbourhood), not a side effect of rebuilding the archive.
- A shared `libc.so.1` needs the tree's exports lists, `so_locations`
  placement and the shared variant (`shareddefs`, `-Bdynamic`), plus a
  rebuilt `libm.so`; none of those sources are in the upload as a complete
  set.
- TLS and LTO stay off for the same reasons, and rld's constraints on both
  are not yet measured.

Until then the static proof is the runtime deliverable, and the harness's
dynamic case continues to use SGI's libc.

## Tests and evidence

Host-only tests for the build's pure pieces:

```sh
python3 scripts/runtime/test-smake.py          # the smake evaluator
python3 scripts/runtime/test-compat.py         # the construct translations
python3 scripts/runtime/test-rebuild-libc.py   # flags, the guest retrieval contract and the ELF proof
```

The guest phase and the readelf proof are fail-closed (issue #5, audit C10):
retrieval is the shared `scripts/lib/guest-txn.sh` transaction, host result
files are cleared before it runs, every retrieve is required, the status must
be a non-negative integer, and a readelf run whose exit status is non-zero or
whose output is empty cannot count as proof of the static shape.
`test-rebuild-libc.py` injects each of those failures through the fake rig
without a guest; the controlled rerun of the whole driver is the acceptance
evidence and stays under `.scratch/runtime/`, recorded separately from the
host fault tests.

Evidence from the acceptance run (`.scratch/runtime/`): the generated tree,
the per-source objects, `libc.a`, `libm.a`, `csu/`, `hello-static`,
`readelf.txt` (header, program headers and dynamic section proving no
`PT_INTERP`/`NEEDED`), `hello.stdout`/`hello.status` from the guest, and the
per-failure logs (none on a clean run). The archives and binary are SGI
material's build products and stay outside the repo (ADR-0001).
