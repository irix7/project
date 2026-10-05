# The IRIX toolchain and its patch series

`scripts/build-toolchain.sh` builds the project's `mips-sgi-irix6.5` cross:
binutils 2.20.1 from the pdaxrom lineage plus an in-repo GCC IRIX layer,
carried as a patch series against the upstream release (CONTEXT.md's *patch
series*). The default target is GCC 16.2.0, the project's chosen compiler
(ADR-0002); `--gcc` selects the 15.3.0 or 15.2.0 fallback recipe.

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

That builds binutils 2.20.1 and GCC 16.2.0 into
`.scratch/toolchain-16.2.0/prefix`, configured against the captured 6.5.7m
sysroot with big-endian o32, n32 and n64 multilibs and o32 as the default
ABI (ADR-0003). The default work directory is version-separated:
`<work root>/toolchain-<gcc version>`, where the work root is
`IRIX_WORK_ROOT` when set and `<repo>/.scratch` otherwise. The flake's
`nix run` exports `IRIX_WORK_ROOT="$PWD/.scratch"`, so it follows the
caller's tree and `nix run . -- --gcc 15.3.0` lands in
`.scratch/toolchain-15.3.0`; an explicit `--work-dir` always wins.
`--languages c` is all the smoke harness needs; adding
`c,c++` builds the libstdc++ IRIX layer as well, which is carried for the
C++ deliverable but not exercised by the C-only acceptance (issue #6). A
`c,c++` build currently stops at libstdc++'s n64 multilib configure because
there is no n64 link target on the rig; n64 is out of scope (ADR-0003 scope
note), so the C++ multilib cut is knowingly deferred and the C layer is
unaffected.

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

Binutils stays 2.20.1 with the two pdaxrom patches; GCC 16.2 did not force
a change there.

The work directory defaults to `<work root>/toolchain-<gcc version>` so a
default build can never overwrite another version's tree, in particular the
original `.scratch/toolchain` 15.2 baseline. The work root is
`IRIX_WORK_ROOT` when set (the flake app exports `$PWD/.scratch`) and
`<repo>/.scratch` otherwise. `--work-dir` and `--prefix` still override
both.

## Resumption and build identity

Resume state is bound to the bytes that produced it (issue #26). A build
is reused only when the script can prove all of the following still match:

- the component and GCC version, the pinned tarball sha256 and the recipe
  (`series`, `pdaxrom`);
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
  0004-irix-libgcc-and-runtime.patch             gthr-posix, libgcov, libgomp
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

The series applies with `patch -p1` from the `gcc-16.2.0` source root, in
`series` order; `build-toolchain.sh` records the applied bytes and their
sha256 per patch, so a re-run skips what is already applied and a changed
patch is reversed and reapplied (see *Resumption and build identity*).

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

Issue #6's evidence (pinned checksums, porting notes, build logs, verify
output, smoke logs and readelf proof) lives under
`.scratch/gcc-16.2-evidence/`; nothing captured from the guest is committed
(ADR-0001).
