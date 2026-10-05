# The smoke harness

`scripts/smoke.sh` is the project's primary testing seam (CONTEXT.md). One
command takes a C source and the exact stdout it must produce, compiles and
links it with the cross under test, ships the binary to the guest with
iris-ci, runs it there and diffs the guest's stdout against the expected
file. Later toolchain milestones test themselves through it; the case data
lives beside it (`scripts/smoke/`), not inside it.

## Invocation

```sh
scripts/smoke.sh [--prefix DIR] [--sysroot DIR] [--abi o32|n32] \
	[--cflags "..."] [--timeout SECONDS] SOURCE EXPECTED
```

The first case is the oracle's own program, so the cross's output can be
diffed against the output MIPSpro recorded in the guest (`hello.o32.output`):

```sh
scripts/smoke.sh --cflags "-lm" oracle/hello.c scripts/smoke/hello.expected
```

On success the harness prints the program's stdout and exits 0:

```
hello from MIPSpro
sqrt(2) = 1.414214
```

`hello.expected` is byte-for-byte the oracle's recorded o32 output. `--abi`
defaults to o32, the Indy's native environment (ADR-0003), and is mapped to
`-mabi=32|n32`. n64 is emitted by the multilib but outside the project's
scope — the IP22/R4400 cannot execute it — so the harness rejects it rather
than offering an unrunnable case (ADR-0003 scope note).
`--cflags` is word-split and passed to both the compile and the link step, so
`-O2`, `-lm` and `-pthread` need no script change. `--timeout` bounds each
iris-ci call (default 300 seconds).

## Dynamic first

The link is dynamic by design (ADR-0006). IRIX 6.5 ships no static libc — its
nonshared archives are tagged `noship`, and there is no nonshared `crt1.o` —
so the only shipping model is to link SGI's `crt1.o`, `libc.so` and `libm.so`
from the captured sysroot. The harness runs the cross's own `readelf` and
refuses a binary without `PT_INTERP`, which keeps a future `-static` case out
of this seam. That proof now lives in the rebuilt runtime (`docs/runtime.md`,
issue #9) as a static o32 link against the tree's own startfiles and archive.

The cross must be configured against the captured sysroot, so the harness
defaults `--sysroot` to the path the compiler prints with `-print-sysroot` and
refuses an empty tree or one without `usr/include`. The current (GCC 16.2)
rebuild is:

```sh
nix develop --command bash -c \
	'scripts/build-toolchain.sh --work-dir .scratch/toolchain-16.2 \
		--sysroot /mnt/europa/sgi-toolchain-scratch/rig/oracle/sysroot --languages c'
scripts/verify-toolchain.sh --prefix .scratch/toolchain-16.2/prefix \
	--gcc-version 16.2.0
```

That recipe is issue #6; the series layout and provenance are in
`docs/toolchain.md`. smoke.sh defaults `--prefix` to
`.scratch/toolchain-16.2/prefix`, the cross under test. The original 15.2
baseline at `.scratch/toolchain/prefix` stays reproducible with
`--gcc 15.2.0 --work-dir .scratch/toolchain` and can still be exercised with
`smoke.sh --prefix .scratch/toolchain/prefix`.

`--languages c` is deliberate: C is all this seam needs and libstdc++ is not a
deliverable, so the rebuild stays quick. A sysroot-less cross still compiles
objects but cannot link IRIX executables; this rebuild is what makes
`crt1.o`, `libc.so` and `libm.so` resolve.

The script's sysroot branch does two things the naive build needs: it
configures binutils with the matching `--with-sysroot` (otherwise the `ld` it
installs rejects the `--sysroot` the sysroot-configured GCC passes — its
`--help` advertises the option either way, so GCC's configure is fooled), and
it skips libatomic while the capture has `pthread.h` but not the o32 and n32
`libpthread.so`. Both are handled there, so one command from a clean checkout
gives the harness its cross; libatomic returns automatically once #18 adds
`libpthread.so` to `sysroot.files`.

## Preconditions and exit behaviour

Cheap, local checks run before the guest is woken, and each failure exits
non-zero with a message on stderr:

- `SOURCE` and `EXPECTED` exist;
- the prefix has `${TARGET}-gcc` and `${TARGET}-readelf`;
- the sysroot is configured, exists, is non-empty and has `usr/include`;
- the rig has its `iris`/`iris-ci` binaries and answers `ping`.

Compile and link failures fail the harness. A guest non-zero exit fails the
harness and prints the guest's stdout and stderr. A stdout mismatch fails with
a unified diff (`diff -u EXPECTED ACTUAL`). Success prints the program's
stdout and exits 0; diagnostic stderr from the program is passed through
without affecting the comparison, which is stdout-only.

The whole guest transaction — `mkdir`, `put`, `run`, `get` — takes lib.sh's
shared guest lock (`$RIG_DIR/guest.lock`, an hour's wait by default), so the
harness serialises with every client that takes the same lock. The binary and
its captured streams live under `/tmp/smoke/` on the guest; the host copies
are in a temporary directory removed on exit.

## Later milestones

Cases are just a source, an expected file and the flags they need:

```sh
# #18 threaded case, once libpthread.so is in the capture:
scripts/smoke.sh --cflags "-pthread" path/to/threads.c scripts/smoke/threads.expected
```

`-pthread` is the seam working as intended: no harness change. The link will
not resolve until `/usr/lib/libpthread.so` and `/usr/lib32/libpthread.so` are
added to `scripts/rig/sysroot.files` and the sysroot recaptured (ADR-0006).
Other milestones call the same command with their own `--cflags` and `--abi`.

## Tests and evidence

The guest-free logic has unit tests:

```sh
python3 scripts/smoke/test-smoke.py
```

The #5 bring-up evidence — the rebuilt cross's `-print-sysroot`, `readelf -d`
proof that hello is dynamic, the guest run, and the three failure cases — is
condensed in the issue report and kept under `.scratch/smoke-evidence/`.
Nothing captured from the guest is committed (ADR-0001): the repo carries the
harness, fixtures and this file.
