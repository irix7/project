# Binutils for the IRIX cross (issue #21)

`scripts/build-toolchain.sh` builds **vanilla GNU binutils 2.47** for
`mips-sgi-irix6.5` by default; `--binutils 2.20.1` still selects the
pdaxrom-patched seed fallback unchanged, for bisecting and for reproducing
pre-2.47 prefixes. The selector, its pins and the build identity are
described in [toolchain.md](toolchain.md); this document records the
vanilla-first evidence for 2.47, the comparison against the community
candidate fixes and the decision that 2.47 carries no patches.

## Pin and provenance

| item | value |
| --- | --- |
| release | GNU binutils 2.47 (`GNU Binutils 2.47.20260726`) |
| tarball | `binutils-2.47.tar.xz`, `https://ftp.gnu.org/gnu/binutils/` |
| sha256 | `154ab23b60070e8f27013c22977f1129425d67d1e8acd6e13010e617811e4cff` |
| sha512 | `3126a1064374d8da40d4d70630c204ed1e75d542c447d53fca9778c7ceff095c28e9b445e15a313fef9729082d7966471ee6b5b715d479aa6d568528743e1d98` |
| checksum list | `https://sourceware.org/pub/binutils/releases/sha512.sum` |

The sha256 was computed from the official tarball after checking it against
the official sha512 list above. The 2.20.1 pin and its two pdaxrom patches
(`binutils-2.20.1-irix.diff`, `binutils-2.20.1-arm64-build-fix.diff`, both
pinned by sha256) are unchanged.

`gas`, `ld`, `bfd` and the binutils programs for the target configure from
the unmodified release: upstream still ships the binutils IRIX target
stanzas, and `ld -V` reports the `elf32bsmip` (o32), `elf32bmipn32` (n32)
and `elf64bmip` (n64) emulations. No target restoration patch is needed,
which disproves the removal premise at the build level as well as at
configure time. n64 is emitted but, per ADR-0003, outside acceptance
scope.

The build was:

```sh
nix develop --command bash -c \
	'../binutils-2.47/configure \
		--prefix=<work>/prefix \
		--target=mips-sgi-irix6.5 \
		--enable-multilib \
		--disable-nls \
		--disable-werror \
		--disable-gdb \
		--disable-sim \
		--disable-gprof \
		--disable-gold \
		--with-sysroot=<capture> && make -j"$(nproc)" && make install'
```

The installed tools identify as `GNU assembler (GNU Binutils)
2.47.20260726` and `GNU ld (GNU Binutils) 2.47.20260726`. Unlike 2.20.1,
2.47's `ld` accepts `--sysroot` in any build; `--with-sysroot` is kept in
the recipe so the default matches the 2.20.1 path, not because 2.47
requires it.

## Host verification

`scripts/test-binutils-vanilla.sh` is the committed, guest-free regression.
It takes a cross prefix and a candidate binutils prefix (defaults: the
cross prefix itself, since `build-toolchain.sh` installs both into one
prefix) and runs three probes:

1. **identity**: `as`/`ld` report GNU binutils 2.47 and the three IRIX
   emulations.
2. **emission**: a header-free probe assembles for o32 and n32; ELF
   class/endianness/ISA flags and the canonical MIPS relocation types are
   checked (`R_MIPS_CALL16`, `R_MIPS_GOT16`, HI16/LO16 for o32;
   `R_MIPS_GOT_DISP`, `R_MIPS_GPREL16`, `R_MIPS_CALL16` for n32).
3. **link**: `oracle/hello.c` links against the captured sysroot for both
   ABIs; the driver trace must name the candidate `ld` and the IRIX
   startfiles (`crt1.o`, `irix-crti.o`, `crtbegin.o`, `crtend.o`,
   `irix-crtn.o`), and the binary must request `/usr/lib/libc.so.1` (o32)
   or `/usr/lib32/libc.so.1` (n32) and depend on `libc.so.1` and
   `libm.so`.

Run against the vanilla 2.47 prefix and the 16.2 cross:

```sh
scripts/test-binutils-vanilla.sh \
	--gcc-prefix .scratch/toolchain-16.2.0/prefix \
	--binutils-prefix <binutils-2.47-prefix>
```

Result on the build host: all checks pass — o32 emits ELF32 big-endian
MIPS II, n32 emits ELF32 big-endian MIPS III with `EF_MIPS_ABI2`, and both
link dynamically with the expected interpreter and startfiles. The same
script also passes against the 2.20.1 prefix with
`--binutils-version 2.20.1`, so the fallback path stays exercised
host-side.

### Why the regression generates a specs file

A cross built by `build-toolchain.sh` bakes the absolute
`--with-as`/`--with-ld` paths into the driver: `find_a_program` returns
`DEFAULT_ASSEMBLER`/`DEFAULT_LINKER` before searching `-B` directories, and
`collect2` prefers `DEFAULT_LINKER` the same way. `-B<binutils-prefix>/bin`
therefore does **not** redirect `as`/`ld` for such a cross; this was
verified by probing a `-B` directory holding wrappers (the compiled-in
paths were still invoked). To exercise a candidate binutils prefix without
rebuilding GCC, the regression rewrites the `*invoke_as` and `*linker` specs
from `gcc -dumpspecs` and proves via `-v` traces that the candidate tools
ran. `--write-specs FILE` keeps that file for the guest smoke:

```sh
scripts/smoke.sh --prefix <gcc-prefix> \
	--cflags "-specs=FILE -lm" oracle/hello.c scripts/smoke/hello.expected
```

The durable path is still a prefix whose GCC was configured against the
selected binutils: `scripts/build-toolchain.sh --binutils 2.47` installs
2.47 and then configures GCC's `--with-as`/`--with-ld` at the new tools.
When run in a work directory whose GCC was already built against 2.20.1,
only binutils is rebuilt and the GCC stanza is reused — its configure
arguments name the same paths and its compiled behaviour does not depend on
the binutils release beyond the GNU-as/ld feature probes already made.

## onre's candidate fixes versus upstream 2.47

The public candidate lives in `onre/binutils-gdb`, branch
`binutils-2_44-irix`, one commit
`4b55be5884a302da12b9a96b92134e3aaabf609a` (2025-04-20), "Added
functionality equivalent to the SGUG-RSE binutils2_23.sgifixes.patch". It
adds a `mips_is_entry_forced_local` helper and restricts
`mips_use_local_got_p`'s "binds locally" branch:

```c
-  if (h->got_only_for_calls
-      ? SYMBOL_CALLS_LOCAL (info, &h->root)
-      : SYMBOL_REFERENCES_LOCAL (info, &h->root))
+  if ((h->got_only_for_calls ? SYMBOL_CALLS_LOCAL(info, &h->root)
+                             : SYMBOL_REFERENCES_LOCAL(info, &h->root))
+      && mips_is_entry_forced_local(h))
     return true;
```

The stated origin is `sgidevnet/sgug-rse`'s
`packages/binutils/binutils2_23.sgifixes.patch` against 2.23.2 (143 lines).
That patch's surviving semantic hunk is the same inversion: only
forced-local symbols (plus undefined symbols, `dynindx == -1`) belong in
the local GOT, where upstream also puts any symbol that binds locally. In
2.47 the decision points are `mips_elf_count_got_symbols` ("Make a final
decision about whether the symbol belongs in the local or global GOT") and
`mips_elf_calculate_relocation`, both routed through
`mips_use_local_got_p`; the onre port is therefore a faithful
re-derivation of the SGUG intent against the refactored code. The rest of
the SGUG patch is obsolete:

| SGUG 2.23 hunk | 2.47 status |
| --- | --- |
| `bfd.c` NULL `elf_section_data` guard | not upstream; malformed-input robustness only, not carried |
| `.rld_map` NULL guard after the assert | not upstream; malformed-input robustness only, not carried |
| `readelf.c` `__sgi` printf workaround | superseded; native-IRIX host only (out of scope) |
| `ld/configure.tgt` `NATIVE_LIB_DIRS` | native IRIX ld only (our links are host-driven) |
| `libtool.m4` rpath nativisation | native build only |
| `pex-unix.c` `pid_t` return, `config.guess` aarch64 | fixed upstream long before 2.47 |

Upstream 2.47 does not contain the forced-local restriction. A probe was
run to demonstrate what the candidate changes on 2.47: an n32 object with
a defined, preemptible global links as a shared library with
`-shared -nostdlib -Wl,-Bsymbolic`. Vanilla 2.47 keeps the symbol in the
local GOT (`MIPS_LOCAL_GOTNO 4`, `MIPS_GOTSYM 0x6`); the onre build moves
it to the global GOT (`MIPS_LOCAL_GOTNO 3`, `MIPS_GOTSYM 0x5`, `.got`
words reordered). The divergence is real but only demonstrates that the
patch changes GOT classification; it does not show vanilla 2.47 failing
for IRIX. Under the vanilla-first policy the fix is therefore **not
applied**: it is a candidate to re-derive (both hunks apply to 2.47 at a
+66-line offset with no fuzz) if the controlled guest smoke or the MIPSpro
oracle shows the SGUG-class failure. Recorded decision: **no patches,
vanilla 2.47**, with `--binutils 2.20.1` as the fallback.

## Guest smoke for the integrator

These are the controlled in-guest checks, to run on the rig; nothing here
was run in a guest by this branch. They are guest evidence, distinct from
the synthetic host probes above.

Against a prefix whose GCC consumes the 2.47 tools (durable path):

```sh
nix develop --command bash -c \
	'scripts/build-toolchain.sh --binutils 2.47 \
		--sysroot /mnt/europa/sgi-toolchain-scratch/rig/oracle/sysroot \
		--languages c'
scripts/smoke.sh --prefix .scratch/toolchain-16.2.0/prefix \
	--cflags "-lm" oracle/hello.c scripts/smoke/hello.expected
scripts/smoke.sh --prefix .scratch/toolchain-16.2.0/prefix --abi n32 \
	--cflags "-lm" oracle/hello.c scripts/smoke/hello.expected
```

Against the existing 2.20.1-built prefix and a separate 2.47 binutils
prefix (one variable changed):

```sh
scripts/test-binutils-vanilla.sh \
	--gcc-prefix .scratch/toolchain-16.2.0/prefix \
	--binutils-prefix <binutils-2.47-prefix> \
	--write-specs /tmp/binutils-2.47.specs
scripts/smoke.sh --prefix .scratch/toolchain-16.2.0/prefix \
	--cflags "-specs=/tmp/binutils-2.47.specs -lm" \
	oracle/hello.c scripts/smoke/hello.expected
scripts/smoke.sh --prefix .scratch/toolchain-16.2.0/prefix --abi n32 \
	--cflags "-specs=/tmp/binutils-2.47.specs -lm" \
	oracle/hello.c scripts/smoke/hello.expected
```

Both ABIs must print the expected `oracle/hello.expected` output. A pass
closes the "controlled guest smoke" criterion for #21 with 2.47 vanilla; a
failure should be recorded against the candidate fix above before any
patch is carried.

## Publication

`irix7/binutils-gdb` receives the series (or, as here, the explicit
no-patch result) as a maintainer push step; this branch does not push. The
provenance to carry across is the pin table above, the onre commit and the
SGUG origin, and this document as the decision record. No SGI or
licence-restricted material is involved; binutils is GPL.
