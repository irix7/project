# Issue #6 porting notes and evidence

Built by the #6 agent. Promoted from the scratch area on 2026-10-07.

## Source pinning

gcc-16.2.0.tar.xz, downloaded from
https://gcc.gnu.org/pub/gcc/releases/gcc-16.2.0/gcc-16.2.0.tar.xz

- sha256 `e6738e29597f733270731aa90600f37ffdc045079dfc27ec7e8192cc81085c3e`
- sha512 cross-checked against the official `sha512.sum`
  (`sha512.sum.gcc-16.2.0` here): OK.

Fallback release gcc-15.3.0.tar.xz:

- sha256 `fa59c1beef8995f27c4d71c1df227587189315d3e6faff1bb4306e61b0c530eb`
- sha512 against `sha512.sum.gcc-15.3.0`: OK.

The sha512.sum files are the official checksum files from
gcc.gnu.org/pub/gcc/releases/gcc-<version>/ (GNU publishes no sha256 list;
the sha256 values in the script are computed from the verified tarballs).

## How the 16.2 series was rebased

Scratch tree `.scratch/gcc-16.2-rebase/gcc-16.2.0`:

1. Extracted the pristine 16.2 tarball and `git init` + commit
   (`pristine` tag).
2. Applied pdaxrom `gcc-15.2.0-irix.diff`, `gcc-15.2.0-irix65-abi64.diff`,
   `gcc-15.2.0-irix65-stdc++.diff` and local patches 0001/0002 with
   `patch -p1`. Only `gcc/configure.rej` and `gcc/configure.ac.rej`
   remained; every other hunk applied with offsets only.
3. Re-derived the TLS hunk: 16.2 inserted a Windows @secrel32 block before
   `if test $set_have_as_tls`, so the IRIX `set_have_as_tls=no` case was
   re-inserted after it. The pdaxrom hunk's `*-*-osf*` case was dropped
   (alpha-dec-osf removed upstream).
4. Dropped autotools churn: `gcc/aclocal.m4`, `libstdc++-v3/aclocal.m4`
   and the libstdc++ `Makefile.in` files (automake 1.15.1 -> 1.15 strings
   only), plus the two `gcc/configure` hunks that only shift libtool
   `#line` markers.
5. Generated `patches/gcc-16.2/` with `make-series.sh` and verified a
   fresh extract of the 16.2 tarball takes all eight patches with
   `patch -p1 --batch --forward` and produces a tree byte-identical to the
   rebase tree (`cmp` over every changed/new path).

Rebase attempt logs: `apply-main.log` in the scratch rebase dir.

## Build commands

```
nix develop --command bash -c \
  'scripts/build-toolchain.sh --work-dir .scratch/toolchain-16.2 \
     --sysroot /mnt/europa/sgi-toolchain-scratch/rig/oracle/sysroot --languages c'
```

Log: `build.log` and `.scratch/toolchain-16.2/logs/{binutils,gcc}.log`.

## Verify and smoke results

```
scripts/verify-toolchain.sh --prefix .scratch/toolchain-16.2/prefix --gcc-version 16.2.0
  -> all checks passed (verify.out)

scripts/smoke.sh --prefix .scratch/toolchain-16.2/prefix --cflags "-lm" oracle/hello.c scripts/smoke/hello.expected
  -> rc 0, stdout matches hello.expected (smoke-o32.out)

scripts/smoke.sh --prefix .scratch/toolchain-16.2/prefix --abi n32 --cflags "-lm" oracle/hello.c scripts/smoke/hello.expected
  -> rc 0, stdout matches hello.expected (smoke-n32.out)

python3 scripts/smoke/test-smoke.py
  -> 9 tests OK (test-smoke.out)
```

`readelf-proof.txt` records the ELF class/endianness/ISA flags and PT_INTERP +
DT_NEEDED for o32 and n32 executables, and the n64 object.

n64: objects compile as ELF64 big-endian MIPS IV (verify's n64 check passes),
but the current sysroot capture cannot link an n64 executable: there is no
`/usr/lib64/mips4/crt1.o` and no n64 libc (only `/usr/lib64/abi/libc.so`,
which has unresolved references). The same link fails with the 15.2 baseline
compiler against the same capture, so it is a capture gap, not a 16.2
regression; n64 execution is issue #20's territory.

## READY handshake

`.scratch/toolchain-16.2/READY` (copy: `READY.txt`):

```
prefix: /home/matt/projects/sgi-toolchain-new/.scratch/toolchain-16.2/prefix
gcc: mips-sgi-irix6.5-gcc (GCC) 16.2.0
status: GCC 16.2.0 release series built; verify o32/n32/n64 passed; dynamic smoke o32 and n32 passed on the guest
```

## 15.3.0 fallback applicability

Sparse dry run in `.scratch/gcc-15.3-sparse/`: the pdaxrom main, abi64 and
stdc++ diffs plus local patches 0001/0002 all applied to a partial extract
of gcc-15.3.0 with `patch -p1 --batch --forward`, no rejects, no fuzz
reported. The fallback was not built end to end (16.2 did not block).

## Optional C++ validation

A second build with `--languages c,c++` was run in
`.scratch/toolchain-16.2-cxx` (separate prefix, so the READY prefix was
untouched). It compiled GCC + the C++ front end, configured the o32 and
n32 libstdc++ variants, then failed while configuring the **n64**
libstdc++ variant:

```
checking for shl_load... configure: error: Link tests are not allowed
after GCC_NO_EXECUTABLES.
```

`cxx-n64-libstdc++-config.log` shows why: GCC_NO_EXECUTABLES' initial
link test cannot link an n64 executable against this sysroot (no
`/usr/lib64/mips4/crt1.o`, no n64 libc), so all later link tests abort.
That is the same pre-existing capture gap recorded above (issue #20),
not a fault in the 16.2 series: the error is a plain n64 link failure in
upstream libstdc++ configure, and no series hunk is involved. The C-only
acceptance does not build libstdc++ at all, so this does not affect it.
The optional validation is therefore blocked on the n64 capture;
`.scratch/toolchain-16.2-cxx/` was removed after collecting
`build-cxx.log` and both config logs.
