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
	'scripts/build-toolchain.sh --work-dir .scratch/toolchain-16.2.0 \
		--sysroot /mnt/europa/sgi-toolchain-scratch/rig/oracle/sysroot --languages c'
scripts/verify-toolchain.sh --prefix .scratch/toolchain-16.2.0/prefix \
	--gcc-version 16.2.0
```

That recipe is issue #6; the series layout and provenance are in
`docs/toolchain.md`. smoke.sh defaults `--prefix` to
`.scratch/toolchain-16.2.0/prefix`, the cross under test. The original 15.2
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
it disables libatomic explicitly. libatomic's configure links a target
executable, and the GCC 17 fork's IRIX specs pass SGI-ld flags
(`-no_unresolved`) that binutils 2.47's GNU ld rejects because the spec's
`IRIX_USING_GNU_LD` branch is never selected (issue #150); libatomic is not
needed by the rebuild yet. Both are handled there, so one command from a
clean checkout gives the harness its cross.

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

The guest phase is the fail-closed transaction below, not a sequence of
unchecked iris-ci calls: a failed `put`, `run` or `get` fails the harness
even when matching output files exist, and the guest's own exit status is
checked separately from the transport. The binary and its captured streams
live under `/tmp/smoke/` on the guest under a name unique to the attempt; the
host copies are in a temporary directory removed on exit.

## Fail-closed guest transaction

`scripts/lib/guest-txn.sh` is the one guest transaction every harness uses:
`mkdir`, each `put`, the `run`, each `get`. It takes lib.sh's shared guest
lock for the whole sequence (`$RIG_DIR/guest.lock`, an hour's wait by
default), so it serialises with every other client; a caller already holding
the lock (re-entrant through `RIG_GUEST_LOCK_HELD`) is safe.

The contract:

- every stage is bounded by the one `--timeout` and its iris-ci status is
  checked. A non-zero status fails the transaction, even when earlier stages
  have already written output — a failed transport cannot pass because
  stdout or a status file exists;
- every host evidence path is removed under the lock before the first
  transport call, and a failed `get` removes its partial target. An earlier
  attempt's result, matching or not, can never be read as this attempt's;
- the guest programme's exit status is not the transaction's status. The
  status file is required evidence: the transaction fails unless it was
  retrieved, the file exists, and its content is a non-negative integer.
  Callers check the value; smoke.sh requires zero and a byte-exact stdout
  diff;
- guest evidence is namespaced per attempt (`smoke-$$-$RANDOM` and the
  like), so two clients cannot collide even if the lock were bypassed.

The transaction's exit status is its interface: 0 success; 64 usage; 90 the
run failed at the transport; 91 a put or get failed; 92 a get reported
success but produced no file; 93 the rig is not answering or the guest
directory could not be created; 94 the status file is empty or not a
non-negative integer. Callers must fail on any non-zero status before reading
evidence. `--streams GUEST_BIN HOST_OUT HOST_ERR HOST_STATUS` is the
smoke/runtime shape (ship, run through `sh`, retrieve all three); the generic
`--run COMMAND --get GUEST HOST ... --status GUEST HOST` form is
hinv-gcc.sh's shape. `--help` carries the full interface, and
`scripts/smoke/test-smoke.py` pins every status.

## The maths boundary

The dynamic model is only tested if a call really crosses the library
boundary. `oracle/hello.c` calls `sqrt(2.0)`, a constant the compiler may
fold, so `scripts/smoke/maths-boundary.c` takes its input from `argv` (no
argument means 2.0) and calls `sin` and `sqrt`; no constant folding can
remove those calls, and the dynamic link must carry `libm.so`. Its recorded
output is `scripts/smoke/maths-boundary.expected`.

The host-side proof compiles it at `-O2` for o32 and n32 and asserts, with
the cross's own readelf, that `sin` and `sqrt` stay undefined external
symbols with `R_MIPS_CALL16` relocations and that the linked binaries have
`PT_INTERP` and a `NEEDED libm.so` entry. No guest is touched:

```sh
scripts/test-maths-boundary.sh --prefix .scratch/toolchain-16.2.0/prefix
```

The controlled guest runs are the integrator's step, under the live rig's
lock, for both ABIs:

```sh
scripts/smoke.sh --cflags "-lm" \
	scripts/smoke/maths-boundary.c scripts/smoke/maths-boundary.expected
scripts/smoke.sh --abi n32 --cflags "-lm" \
	scripts/smoke/maths-boundary.c scripts/smoke/maths-boundary.expected
```

Record their stdout and status beside the hello evidence: the host check
proves the external symbol and `NEEDED` shape, the guest rerun proves the
captured library answers.

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

The guest-free logic and the fault matrix have host-only tests; none of them
touches the live rig:

```sh
python3 scripts/smoke/test-smoke.py
```

`test-smoke.py` runs smoke.sh end to end against a fake cross prefix and the
committed fake iris-ci (`scripts/rig/fake-iris-ci.sh`) in a temporary
`IRIX_RIG_DIR`. Each acceptance boundary is injected and must fail: failed
put, run or get, timeout, missing or invalid status, partial output, stale
matching evidence, non-zero guest exit, stdout mismatch, and a readelf
failure before the guest is reached. It also pins the transaction contract
and its exit statuses directly.

These are host fault-injection results, not guest evidence. The #5 dynamic
acceptance evidence is a controlled guest rerun — the rebuilt cross's
`-print-sysroot`, the `readelf -d` proof that hello is dynamic, the guest runs
of `oracle/hello.c` and of `scripts/smoke/maths-boundary.c` for o32 and n32,
with each transaction's stdout and status recorded — condensed in the issue
report and kept under `.scratch/smoke-evidence/`, separate from the host
tests. Nothing captured from the guest is committed (ADR-0001): the repo
carries the harness, fixtures and this file.
