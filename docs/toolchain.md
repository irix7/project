# The IRIX toolchain and its patch series

`scripts/build-toolchain.sh` builds the project's `mips-sgi-irix6.5` cross:
vanilla GNU binutils 2.47 plus an in-repo GCC IRIX layer, carried as a
patch series against the upstream release (CONTEXT.md's *patch series*).
Binutils 2.47 needs no target restoration or fix patches (see
[binutils.md](binutils.md)); `--binutils 2.20.1` still selects the
pdaxrom-patched seed fallback. The default target is GCC 16.2.0, the
project's chosen compiler (ADR-0002); `--gcc` selects the 15.3.0 or 15.2.0
fallback recipe.

The series is the source of truth, not a long-lived fork branch. Every patch
keeps GCC's GPL; no SGI or licence-restricted material is published
(ADR-0001).

## One command

```sh
nix develop --command bash -c \
	'scripts/build-toolchain.sh \
		--sysroot /mnt/europa/sgi-toolchain-scratch/rig/oracle/sysroot \
		--languages c'

scripts/verify-toolchain.sh --prefix .scratch/toolchain-16.2.0/prefix \
	--gcc-version 16.2.0
```

That builds binutils 2.47 and GCC 16.2.0 into
`.scratch/toolchain-16.2.0/prefix`, configured against the captured 6.5.7m
sysroot with big-endian o32, n32 and n64 multilibs and o32 as the default
ABI (ADR-0003). `--binutils 2.20.1` selects the pdaxrom-patched fallback
for the binutils half of the prefix; the selector and the vanilla-first
evidence are in [binutils.md](binutils.md). The default work directory is
version-separated: `<work root>/toolchain-<gcc version>`, where the work
root is
`IRIX_WORK_ROOT` when set and `<repo>/.scratch` otherwise. The flake's
`nix run` exports `IRIX_WORK_ROOT="$PWD/.scratch"`, so it follows the
caller's tree and `nix run . -- --gcc 15.3.0` lands in
`.scratch/toolchain-15.3.0`; an explicit `--work-dir` always wins.
`--languages c` is all the smoke harness needs. The C++ consumer probe (C)
in `scripts/test-hosted-stdint.sh` additionally needs `cc1plus`, which a
C-only cross does not install. A full `c,c++` build stops at libstdc++'s
n64 multilib configure because there is no n64 link target on the rig; n64
is out of scope (ADR-0003 scope note), so the C++ multilib cut is knowingly
deferred and the C layer is unaffected. A C++-capable prefix does not need
the target libraries: configure with `--languages c,c++`
(`scripts/build-toolchain.sh --languages c,c++`) and, in the configured GCC
build tree, run `make all-gcc install-gcc` instead of the full `make`. That
installs `cc1plus` alongside the C front end and stops before libstdc++,
and the stdint regression then runs probe C for o32 and n32 along with
A, B and D.

The smoke harness then proves the dynamic path in the guest (ADR-0006):

```sh
scripts/smoke.sh --prefix .scratch/toolchain-16.2.0/prefix \
	--cflags "-lm" oracle/hello.c scripts/smoke/hello.expected
scripts/smoke.sh --prefix .scratch/toolchain-16.2.0/prefix --abi n32 \
	--cflags "-lm" oracle/hello.c scripts/smoke/hello.expected
```

The multilib still emits n64 objects, but n64 is outside the project's
scope: the IP22/R4400 guest is 32-bit and cannot execute them, and no 64-bit
target is planned (ADR-0003 scope note). `verify-toolchain.sh` therefore
checks o32 and n32 only, the smoke harness accepts only those two ABIs, and
full-tree acceptance records n64 products as exceptions.

## Sysroot overrides

The IRIX startfile, library and endfile specs take every sysroot-derived
path from the driver's runtime `%R`. `%R` expands to the command-line
`--sysroot` when one is given and to the configured `--with-sysroot`
otherwise (the default `gcc -print-sysroot`), so headers (`-isysroot`),
startfiles and the ISA `-L` directories always name the one requested root.
No capture-specific absolute path is baked into the specs: a cross built
against one capture links against another with `--sysroot`, and a
sysroot-less invocation keeps the configured default. The n64 spec branches
use `%R` as well, but n64 linking stays out of scope (ADR-0003 scope note).

`scripts/test-sysroot-override.sh` is the guest-free driver-spec
regression. With two synthetic, public, empty-marker roots it checks that
o32 and n32 `-###` link traces for `--sysroot=B` and `--sysroot=C` name no
path from the configured root and take every startfile and `-L` directory
from the requested root, that `-E -v` searches the requested root's headers
(so headers and runtime inputs converge), and that the configured-root
default still names the expected IRIX startfiles and links a binary with
the expected interpreter (`/usr/lib/libc.so.1` for o32,
`/usr/lib32/libc.so.1` for n32). It needs only a prefix and never copies
sysroot contents:

```sh
scripts/test-sysroot-override.sh --prefix .scratch/toolchain-16.2.0/prefix
```

The check goes GREEN only against a prefix rebuilt from the patched series;
a driver built before the `%R` fix fails the n32 replacement-root probe by
design.

## Pinned upstream sources

The script verifies every download against a pinned sha256, computed from
the official GNU release tarballs after checking them against the official
sha512 list
(`https://gcc.gnu.org/pub/gcc/releases/gcc-<version>/sha512.sum`):

| source | sha256 |
| --- | --- |
| `gcc-16.2.0.tar.xz` | `e6738e29597f733270731aa90600f37ffdc045079dfc27ec7e8192cc81085c3e` |
| `gcc-15.3.0.tar.xz` | `fa59c1beef8995f27c4d71c1df227587189315d3e6faff1bb4306e61b0c530eb` |
| `gcc-15.2.0.tar.xz` | `438fd996826b0c82485a29da03a72d71d6e3541a83ec702df4271f6fe025d24e` |
| `binutils-2.47.tar.xz` | `154ab23b60070e8f27013c22977f1129425d67d1e8acd6e13010e617811e4cff` |
| `binutils-2.20.1.tar.bz2` | `71d37c96451333c5c0b84b170169fdcb138bbb27397dc06281905d9717c8ed64` |

The binutils pins are checked against the official sha512 list at
`https://sourceware.org/pub/binutils/releases/sha512.sum`; 2.47 is vanilla
(no patches) and 2.20.1 keeps its two pdaxrom patches unchanged. See
[binutils.md](binutils.md) for the onre comparison and the no-patch
decision.

The work directory defaults to `<work root>/toolchain-<gcc version>` so a
default build can never overwrite another version's tree, in particular the
original `.scratch/toolchain` 15.2 baseline. The work root is
`IRIX_WORK_ROOT` when set (the flake app exports `$PWD/.scratch`) and
`<repo>/.scratch` otherwise. `--work-dir` and `--prefix` still override
both.

## Resumption and build identity

Resume state is bound to the bytes that produced it (issue #26). A build
is reused only when the script can prove all of the following still match:

- the component (binutils or GCC), its version, the pinned tarball sha256
  and the recipe (`vanilla`, `series`, `pdaxrom`), plus the binutils
  selector's patch digests when the pdaxrom recipe is selected;
- the sha256 of every patch in apply order (in-repo patches hashed from
  their bytes, remote patches from their pinned checksums, so identity is
  knowable without a download);
- the languages, configure arguments and sysroot path;
- the actual installed tools: both completions record the `--version`
  output of `${PREFIX}/bin/mips-sgi-irix6.5-as` and `-ld` (binutils) or
  `-gcc` (GCC), and reuse also re-runs those tools to check they still
  report it.

Each component's completion record is
`stamps/<component>.installed.identity` plus `.output`; a stamp without a
matching identity or captured output is a partial build and is completed
(down to a full reconfigure when the requested inputs changed) rather than
trusted. `configure_and_make` records the same identity beside the build
tree, so a changed patch, option or source wipes the configured tree
instead of reusing stale objects.

Applied patches keep a copy of their bytes at
`<src>/.irix-patched.d/<name>.applied` and their sha256 in `<name>.sha256`.
A same-named patch whose bytes changed is reversed from the stored copy
and the new bytes applied; when the stored bytes cannot be reversed (or an
old filename-only marker has unknown bytes) the script fails and asks for
`--clean`. It never silently keeps the old patch applied, and never
reports an old installed compiler as the new release. Building a different
`--gcc` in a work directory that holds another release's stamps, sources,
tarball or installed compiler fails by name and suggests the
version-separated default or `--clean`.

## Series layout and provenance

```
patches/gcc-16.2/
  series                                    apply order and policy
  0001-irix-target-config-and-startfiles.patch   config.gcc, multilib, crt files
  0002-irix-mips-target-macros.patch             iris.h/iris5.h/iris6.h, mips.h
  0003-irix-mips-codegen.patch                   mips.cc, dwarf2cfi.cc, varasm.cc
  0004-irix-libgcc-and-runtime.patch             libgcov, libgomp; gthr-posix kept upstream
  0005-irix-configure.patch                      configure.ac and configure
  0006-libstdcxx-irix-os-layer.patch             config/os/irix, configure.host
  0007-gcc-stdint-inttypes-guard.patch           ginclude/stdint-gcc.h
  0008-host-build-fixes.patch                    native-IRIX host build fixes
```

The series is derived from pdaxrom/irix-gcc tag 1.2 (`2aa3421b`), the proven
15.2 public port: `gcc-15.2.0-irix.diff` (the main layer),
`gcc-15.2.0-irix65-abi64.diff` (the n32/64/32 multilib set and the stdint
guard) and `gcc-15.2.0-irix65-stdc++.diff` (the c++config hook). SGUG RSE
and onre's `irix/gcc-9.2.0` remain the GPL lineage and semantic oracle for
triage (ADR-0002); no hunk in this series needed re-deriving from them.

Each patch carries a commit-message-style header with a `Provenance:` line
naming the pdaxrom diff and tag it came from; where a patch mixes origins or
needed re-derivation the header also carries per-hunk notes. The main
re-derivations against 16.2 were:

- **Autotools churn dropped.** The pdaxrom diff mixes real source changes
  with regenerated `aclocal.m4`, `configure` and `Makefile.in` files whose
  only change is the automake version string (1.15.1 to 1.15) or shifted
  libtool `#line` markers. None of that is carried; the real configure
  changes are in `0005` and the `libstdc++` generated `configure` hunk in
  `0006`.
- **TLS hunk re-derived.** 16.2 inserted a Windows `@secrel32` check between
  the assembler TLS probe and `HAVE_AS_TLS`, and dropped alpha-dec-osf, so
  the IRIX case was re-inserted after the new block and the pdaxrom OSF case
  was dropped as dead.
- **Local patches folded.** `patches/0001-t-iris-conditional-limits-h.patch`
  and `patches/0002-t-iris6-conditional-limits-h.patch` remove the pdaxrom
  `LIMITS_H_TEST = true` from the two `t-iris*` fragments. The 16.2 series
  simply never adds the assignment (so GCC's default conditional test sees
  the sysroot); the two files remain for the 15.x fallback recipe and their
  provenance (original to this repository) is recorded in `0001`'s header.
- **Dead hook carried.** `MIPS_TFMODE_FORMAT`'s `mips_extended_format` no
  longer exists in 15.2 or 16.2; the define is inert and is carried for
  fidelity rather than re-derived. No hunk changed behaviour when ported:
  the whole series applies to the pristine 16.2 tarball with no rejects.
- **gthr-posix hunks dropped.** The pdaxrom diff commented out IRIX's
  `pthread_rwlock_*`, `pthread_equal`, `sched_yield` and
  `pthread_mutexattr_settype` references, claiming IRIX 6.5 lacks them;
  without the `settype` call libgcc's recursive mutexes were plain mutexes
  (audit finding C14). The captured 6.5.7m headers declare every one of
  them — `pthread_equal` (with its identity macro),
  `pthread_mutexattr_settype` and `PTHREAD_MUTEX_RECURSIVE`, the
  `pthread_rwlock_t` typedef with the `pthread_rwlock_*` operations, and
  `sched_yield`, which the captured `libc.so` already exports as a weak
  symbol — so no hunk was carried. `gthr-posix.h` stays at the upstream
  release, and the target genuinely lacks only the static recursive
  initialiser macros, which upstream already selects
  `__gthread_recursive_mutex_init_function` for.

The series applies with `patch -p1` from the `gcc-16.2.0` source root, in
`series` order; `build-toolchain.sh` records the applied bytes and their
sha256 per patch, so a re-run skips what is already applied and a changed
patch is reversed and reapplied (see *Resumption and build identity*).

## stdint policy for the captured environment

The `mips-sgi-irix6.5` stanza in `0001` selects `use_gcc_stdint=provide`
(issue #30), where pdaxrom wrapped a system header for 6.5. `provide`
installs `ginclude/stdint-gcc.h` directly as the cross's `stdint.h`;
`wrap` installs `ginclude/stdint-wrap.h`, which `#include_next`s the
target's `stdint.h` when hosted. The required 6.5.7m capture has no
`/usr/include/stdint.h` — it ships `inttypes.h`, and only the optional
IRIX Development Foundation 1.3 provides a system `stdint.h` — so under
`wrap` a hosted `#include <stdint.h>` fails at `include_next` for both
o32 and n32. `provide` is already the policy for IRIX 5 and 6.0–6.4, so
6.5 is now consistent with the rest of the stanza.

`provide` also keeps freestanding compilation working: `stdint-gcc.h` is
installed as the header itself rather than pulled in through the wrapper's
`__STDC_HOSTED__` branch. The trade-off is that an installation which does
ship a system `stdint.h` (IDF 1.3) gets GCC's header, because the GCC
include directory precedes the sysroot in the search order. Patch `0007`
keeps that safe: it gates `stdint-gcc.h`'s exact-width and greatest-width
typedefs on `__inttypes_INCLUDED`, the guard the capture's own
`inttypes.h` defines before its typedef block, so either inclusion order
avoids incompatible redefinitions and the limits/constant macros still
come from `stdint-gcc.h` when `inttypes.h` is included first.

`scripts/test-hosted-stdint.sh` is the regression. It compiles hosted
(o32 and n32), include-order, C++ and freestanding probes; it is guest-free
and reads the capture's headers in place. Probes A, B and D run against any
cross; probe C runs for o32 and n32 once the cross has `cc1plus` (see
[One command](#one-command) for the `c,c++` recipe that stops before the
target libraries) and then re-runs automatically. Run it against a rebuilt
prefix as:

```sh
scripts/test-hosted-stdint.sh --prefix .scratch/toolchain-16.2.0/prefix \
	--sysroot /mnt/europa/sgi-toolchain-scratch/rig/oracle/sysroot
```

## stdint oracle comparison

`scripts/test-stdint-oracle.sh` is the guest half of the issue #30
evidence. It compiles `oracle/stdint-probe.c` — an independently authored
probe that names only standard headers and supplies C99's `SIZE_MAX`
itself — with the cross for o32 and n32, ships the probe and both binaries
to a namespaced `/tmp` directory on the guest, runs them under the rig's
shared guest lock, then compiles and runs the same probe in the guest with
native MIPSpro `cc -o32` and `cc -n32` (driven with `rehash`, as in
`oracle.sh`). The two stdout streams are diffed per ABI and any differing
line fails the run.

This is guest evidence, distinct from the guest-free hosted-stdint probes:
it proves the cross's exact widths, pointer-sized types and limits agree
with the real MIPSpro oracle for both ABIs, where the host test only proves
the provided headers compile and match GCC's ABI builtins. It needs the
running rig and the licensed or locally patched MIPSpro guest from
`docs/oracle.md`; a missing prefix, sysroot, rig socket, guest login or
`cc` is reported as a clear skip with no pass, and a transport failure or
any output difference fails. Run it as:

```sh
scripts/test-stdint-oracle.sh \
	--prefix .scratch/toolchain-16.2.0/prefix \
	--sysroot /mnt/europa/sgi-toolchain-scratch/rig/oracle/sysroot
```

Its probe shipping, output parsing, shape checks and per-ABI diff keep
host-only tests with a fake `iris-ci`:
`python3 scripts/test-stdint-oracle.py`.

## gthread capability selection

`libgcc/gthr-posix.h` stays at the upstream release (issue #31): the
captured 6.5.7m headers declare the recursive mutex type, `pthread_equal`,
`sched_yield` and the rwlock contract, so the pdaxrom hunks that commented
them out were dropped rather than replaced with a semantic no-op. The
pthread entry points live in `/usr/lib/libpthread.so` and
`/usr/lib32/libpthread.so`, which `scripts/rig/sysroot.files` now names so
the next capture can link `-lpthread` (that capture also lets
`build-toolchain.sh` build `libatomic` again).

Two regressions keep the selection honest. The guest-free
`scripts/rig/test-gthread-patch.py` checks the patch header and the applied
header, runs the selection logic on the host, compiles it against the
captured headers for o32 and n32, and — once the recaptured sysroot ships
`libpthread.so` — checks the exported symbols by name. The guest probe
builds `scripts/smoke/gthread-recursive.c` for both ABIs with the cross,
ships it through the shared fail-closed transaction with a bounded timeout
and diffs its stdout against the committed expected file. It covers a
double lock and double unlock on one thread, cross-thread exclusion,
recursive attribute initialisation and error handling, `pthread_equal`
(macro and entry point), `sched_yield` and the rwlock contract:

```sh
python3 scripts/rig/test-gthread-patch.py

scripts/test-gthread-recursive.sh \
	--prefix .scratch/toolchain-16.2.0/prefix \
	--sysroot /mnt/europa/sgi-toolchain-scratch/rig/oracle/sysroot
```

A missing prefix, cross, sysroot, rig, guest login or capture
`libpthread.so` is a clear skip; a link failure once the library is
present, a transport failure, a non-zero status or any output difference
fails closed, so an unsupported contract can never be a silent no-op.

## The 15.3.0 fallback (and 15.2.0 baseline)

`--gcc 15.3.0` selects the fallback recipe: the pdaxrom **15.2.0** diffs
applied to the GCC 15.3.0 release plus the local `patches/0001`/`0002`.
It remains a heuristic until a 15.3-specific series is re-derived, but a
sparse dry run (issue #6's scratch `gcc-15.3-sparse/`) applied all three
pdaxrom diffs plus the two local patches to the 15.3.0 release with no
rejects. The path is real rather than
aspirational: `scripts/build-toolchain.sh --gcc 15.3.0` either applies them
or fails loudly on a reject rather than silently mis-applying.

`--gcc 15.2.0` applies the same diffs to the 15.2.0 release and reproduces
the original baseline (the tree under `.scratch/toolchain`, now also
buildable at `.scratch/toolchain-15.2.0`). GCC 16.2 did not block, so this
fallback was not needed for issue #6; it is kept for bisecting and for
anyone reproducing the pre-16.2 state.

## Reproducing or extending the series

The scratch rebase used to prepare this series keeps a pristine extract and
a git-committed baseline, applies the pdaxrom diffs, and regenerates
`patches/gcc-16.2/` from the resulting `git diff` split by theme
(`.scratch/gcc-16.2-rebase/make-series.sh`; scratch only, never committed).
The maintainable recipe for the next release is: extract the new tarball,
`git init` + commit pristine, apply the previous series, triage rejects one
by one against the upstream diff and the SGUG/onre lineage, then re-split.
Do not regenerate through `autoreconf`: the build uses the shipped
`configure`, so generated-file changes are patched in place and documented.

## Tests and evidence

The guest-free harness logic keeps its unit tests:

```sh
python3 scripts/smoke/test-smoke.py
python3 scripts/lib/test-build-identity.py   # resumption identity, fake tools only
```

The binutils vanilla-first evidence (issue #21) has a guest-free
regression that drives a candidate binutils prefix through the existing
cross with a generated specs file, then checks o32/n32 emission, the IRIX
startfiles and the dynamic link. It skips cleanly without the prefixes and
runs against 2.20.1 with `--binutils-version 2.20.1` too; see
[binutils.md](binutils.md):

```sh
scripts/test-binutils-vanilla.sh --gcc-prefix .scratch/toolchain-16.2.0/prefix \
	--binutils-prefix <binutils-2.47-prefix>
```

The common-symbol alignment contract (issue #28) has a guest-free
regression too:

```sh
scripts/test-common-align.sh --prefix .scratch/toolchain-16.2.0/prefix
```

It compiles synthetic ordinary and 64-byte-aligned tentative definitions
for o32 and n32 under `-fcommon` and `-fno-common`, then checks that the
object records the requested alignment (a three-operand
`.comm name,size,align`, or an aligned `.bss`), that the final linked
address is a multiple of it, and that the compiler's optimised
assumption agrees. The series keeps upstream's `true` for
`mips_declare_common_object`'s alignment-operand switch — the toolchain
assembles with GNU as (`--with-gnu-as`), which accepts the three-operand
directive — so common alignment is not weakened for any MIPS target.

The runtime half of that contract has a committed fixture,
`scripts/smoke/common-align.c`, run on the guest through the smoke harness
for both ABIs (the same source links under `-fno-common`, checked host-side):

```sh
scripts/smoke.sh --prefix .scratch/toolchain-16.2.0/prefix \
    --cflags "-O2 -fcommon" scripts/smoke/common-align.c \
    scripts/smoke/common-align.expected
scripts/smoke.sh --prefix .scratch/toolchain-16.2.0/prefix --abi n32 \
    --cflags "-O2 -fcommon" scripts/smoke/common-align.c \
    scripts/smoke/common-align.expected
```

The sysroot-override driver regression is guest-free too; see
[Sysroot overrides](#sysroot-overrides):

```sh
scripts/test-sysroot-override.sh --prefix .scratch/toolchain-16.2.0/prefix
```

The stdint policy (issue #30) has its guest-free regression
(`scripts/test-hosted-stdint.sh`), its guest oracle comparison
(`scripts/test-stdint-oracle.sh`) and host-only tests for the oracle
script's shipping, shape and diff logic:

```sh
python3 scripts/test-stdint-oracle.py
```

See [stdint policy for the captured
environment](#stdint-policy-for-the-captured-environment) and [stdint
oracle comparison](#stdint-oracle-comparison).

The gthread capability selection (issue #31) has its guest-free regression
(`python3 scripts/rig/test-gthread-patch.py`) and its bounded guest probe
(`scripts/test-gthread-recursive.sh` with
`scripts/smoke/gthread-recursive.c` and its expected file); see [gthread
capability selection](#gthread-capability-selection).

Issue #6's evidence (pinned checksums, porting notes, build logs, verify
output, smoke logs and readelf proof) lives under
`.scratch/gcc-16.2-evidence/`; nothing captured from the guest is committed
(ADR-0001).
