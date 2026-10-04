# Reference hinv build (native MIPSpro + smake)

`hinv` is the first real command from the IRIX tree rebuilt natively in the
guest with SGI's own tools (issue #7, ADR-0005). Its binary, object, compiler
transcripts and output are the ground truth a later GCC 16.2 build is diffed
against: the flags below are what the tree's own rules resolved, and the
rebuilt binary's output is byte-identical to the shipped `/sbin/hinv`.

Everything captured here is SGI material and stays outside the repo
(ADR-0001) under `$RIG_ORACLE_DIR/hinv-reference/` (default
`/mnt/europa/sgi-toolchain-scratch/rig/oracle/hinv-reference`). Only
`scripts/rig/hinv-reference.sh` and this document are committed.

## What was built

| | |
|---|---|
| Tree | `irix-6.5.7m-src` at commit `822f4fcb99524a2fa03747acc4c5946eeb63ea3c` (`calmsacibis995/irix-657m-src`), plus the checkout's local `irix/usr/include` supplement |
| Sources | `irix/cmd/hinv/{hinv.c,Makefile}` |
| Guest | IRIX IRIS 6.5 `01200531` IP22 (Indy, 256 MB, R4400) |
| Toolchain | MIPSpro Compilers 7.30 (`cc`), CEE 7.4, Development System 7.2.1, IRIX 6.5 Development Libraries |
| Build tool | `smake` -> `/usr/sbin/pmake`, Parallel Make Utilities (Development System 7.2.1) |
| ABI | ELF N32 MSB mips-3, dynamically linked |

The build is entirely in `/tmp/hinv-reference` inside the guest; nothing is
installed into `/sbin` or `/usr/bin`. The binary is run as `./hinv`, so csh's
`rehash` quirk (new binaries only appear on `PATH` after `rehash`) never
arises.

### Why n32

The guest's rooted make rules pin this release's object style to n32 with the
mips3 ISA, and the matching rld name to the n32 libc with an rpath of
`/lib32`. The guest's shipped reference is the same ABI:

```
$ file /sbin/hinv
/sbin/hinv:     ELF N32 MSB mips-3 dynamic executable MIPS - version 1
$ file irix/cmd/hinv/hinv      # rebuilt
... ELF N32 MSB mips-3 dynamic executable (not stripped) ...
```

The rebuilt `hinv` output is byte-identical to `/sbin/hinv`'s
(`sha256 060f8641...` for both `hinv.output` and `hinv.stock.output`). n32 is
therefore the deliberate choice: it is what the tree's own defaults select on
this release and what the shipped binary is. (The oracle's `hello` capture
still builds o32 and n32 explicitly because it is not a tree build; the IP22
userland also runs o32.)

## How smake drove the build

`hinv`'s Makefile begins with a plain `#`, not the `#!smake` magic used by
`irix/cmd/lboot/Makefile`; it is invoked as `smake` explicitly. It names
`hinv.c` as its only source and `hinv` as its target, sets `LLDOPTS` to carry
the rooted rld name into the link, and pulls in the rooted `commondefs` and
the common rules.

With `ROOT=/` (the tree's own rooted build convention) that resolves to the
guest's `/usr/include/make/commondefs`, which includes `releasedefs`, the
release's styles. `commondefs` composes the flags:

- the global includes are the rooted include directory under `-nostdinc`;
- the global options carry the style, the optimiser (`-O`), the
  dependency-update flag (`-MDupdate Makedepend`) and the standard warning
  suppressions;
- local (`L*`) flags precede global ones in `CFLAGS` — the hook the include
  override uses;
- the n32 link options carry `-nostdlib` and the `/usr/lib32` search path.

Because `TARGETS=hinv` has a null suffix, smake's built-in `.c:` rule compiles
and links in a single `cc` invocation; the `OBJECTS` list (`hinv.o`) is
computed by `commondefs` but not used by the default rule. The object has to
be asked for as an explicit target (`smake ... hinv.o`, the `.c.o` rule) to be
kept, which the script does.

### What had to be overridden, and why

- `ROOT=/` — the tree's makefiles are rooted at `$(ROOT)`; this is the stock
  convention, not a workaround.
- `LCINCS="..."` — the Development Libraries install is incomplete for a
  command that includes kernel headers and `diskinvent.h` (see below). The
  override adds the staged tree directories *before* the global includes, the
  intended way to add local includes (`CFLAGS` puts `L*` before `G*`).
  `GCINCS` itself is untouched.
- `CC="cc -v"` — only for the transcript; it makes the driver print its
  phase-level lines and the final link.

All commands are run through `env` because the guest shell is csh.

## The exact build lines

Dry run, stock resolution (`env ROOT=/ smake -n`, before overrides):

```
--- hinv ---
	/usr/bin/cc         -nostdinc -I//usr/include -mips3 -n32 -O  -MDupdate Makedepend -woff 1685,515,608,658,799,803,852,1048,1233,1499 hinv.c  -Wl,-I,/lib32/libc.so.1,-rpath,/lib32  -mips3 -n32 -quickstart_info -nostdlib -L//usr/lib32/mips3 -L//usr/lib32 -L//usr/lib32/internal     -o hinv
--- default ---
```

Dry run with the include override (`env ROOT=/ smake -n "LCINCS=..."`), the
command that was actually built:

```
--- hinv ---
	/usr/bin/cc      -I/tmp/hinv-reference/irix/kern -I/tmp/hinv-reference/irix/usr/include   -nostdinc -I//usr/include -mips3 -n32 -O  -MDupdate Makedepend -woff 1685,515,608,658,799,803,852,1048,1233,1499 hinv.c  -Wl,-I,/lib32/libc.so.1,-rpath,/lib32  -mips3 -n32 -quickstart_info -nostdlib -L//usr/lib32/mips3 -L//usr/lib32 -L//usr/lib32/internal     -o hinv
--- default ---
```

The compile-only rule for the object:

```
--- hinv.o ---
	/usr/bin/cc      -I/tmp/hinv-reference/irix/kern -I/tmp/hinv-reference/irix/usr/include   -nostdinc -I//usr/include -mips3 -n32 -O  -MDupdate Makedepend -woff 1685,515,608,658,799,803,852,1048,1233,1499 -c hinv.c
```

The driver's verbose transcript splits that one command into its phases: the
front end (`fec`, with `-O2` and `-TARG:abi=n32:isa=mips3`) then the back end
(`be`, emitting `hinv.o`). The full phase lines are in `smake.verbose.log`.

Link (verbatim):

```
/usr/lib32/cmplrs/ld32 -call_shared -no_unresolved -transitive_link -elf -_SYSTYPE_SVR4 -show -MDupdate Makedepend -woff1685,515,608,658,799,803,852,1048,1233,1499 -I /lib32/libc.so.1 -rpath /lib32 -mips3 -n32 -quickstart_info -L -L//usr/lib32/mips3 -L//usr/lib32 -L//usr/lib32/internal -MDignore hinv.o //usr/lib32/mips3/crt1.o -o hinv hinv.o -dont_warn_unused -Bdynamic -lc //usr/lib32/mips3/crtn.o -warn_unused
```

The object list is just `hinv.o`. The `crt1.o`/`crtn.o` startfiles and `-lc`
on the `ld32` line come with the driver's handling of the `ROOTRLDNAME`
marker (`-Wl,-I,/lib32/libc.so.1,-rpath,/lib32`) even though the driver command
carries `-nostdlib`; that combination is exactly what the tree's rules produce
for a dynamically linked n32 command.

The compiler emits the licence banner and a number of warnings (a dead
`Location`, an `-OPT:Olimit` overflow on `display_item`); they are verbatim in
`smake.verbose.log` and do not fail the build.

## The missing headers

The Development Libraries install does provide `/usr/include/sys/EVEREST/`
(`addrs.h`, `evconfig.h`, `everest.h`, `evmp.h`, `evdiag.h`, ...), but not the
kernel-only string table `diagval_strs.i` that `hinv.c` includes at line 51.
It also ships `/usr/include/invent.h` without `diskinvent.h`, which `hinv.c`
includes at line 24. Both exist in the tree:

- `irix/kern/sys/EVEREST/diagval_strs.i` (and its dependency
  `irix/kern/sys/EVEREST/evdiag.h`; the tree copy is byte-identical to the
  guest's installed one)
- `irix/usr/include/diskinvent.h`, which belongs to the checkout's local
  `irix/usr/include` supplement rather than the upstream commit — the tree's
  own build had installed these headers, and the supplement recovers the ones
  the upload lacks.

`scripts/rig/hinv-reference.sh` stages exactly those files under
`/tmp/hinv-reference/<tree path>` and adds
`-I/tmp/hinv-reference/irix/kern -I/tmp/hinv-reference/irix/usr/include` as
`LCINCS`. A stock build fails first on `diskinvent.h` and then on
`diagval_strs.i`; with the override it compiles clean. This is the minimal
kernel-source include path the reconstruction needs.

Because `diskinvent.h` is untracked, the capture records per-file provenance:
`tree-files.txt` lists each staged source as `clean`, `modified` or
`untracked`, and `tree-commit.txt` carries the full revision (suffixed
`-dirty` when tracked files differ). A later GCC build must use the same
revision and files for its output to be comparable.

## lboot

`hinv` does not use `lboot`, and no userland command does: `lboot` is the
kernel configuration and link tool, not part of the command build. The guest's
`man lboot` describes it as the tool that reads the kernel's master files,
extracts and compiles the selected configuration, and links the result with
the kernel object files to produce a bootable kernel. For a later kernel
rebuild (ADR-0005) it therefore requires the master files for the devices and
options configured, the built kernel objects, the CPU install directory and
the stunefile of tunables; none of that applies to a userland command. The
tree agrees: its kern definitions point `LBOOT` at the guest's
`/usr/sbin/lboot`, and only the kernel targets invoke it.

The hinv transcript contains only `cc` -> `fec`/`be`/`ld32`; no `lboot`. The
tool itself ships in `compiler_eoe.sw.lboot` (installed as
`/usr/sbin/lboot`), so a kernel rebuild later will have it, but the userland
reference build neither needs nor touches it.

## Captured tree

Under `$RIG_ORACLE_DIR/hinv-reference/`, checksummed by `manifest.sha256`:

| Path | What it is |
|------|------------|
| `tree-commit.txt` | tree HEAD the build came from |
| `tree-files.txt` | git status of each staged source (`clean`/`modified`/`untracked`) |
| `environment.txt` | `uname -a`, `cc -version`, `versions` products, smake identity, `file` ABI proof for stock and rebuilt binaries |
| `smake.dryrun.log` | `env ROOT=/ smake -n`, stock resolution |
| `smake.dryrun.overrides.log` | dry run with the `LCINCS` override, binary and object rules |
| `smake.verbose.log` | real build with `CC="cc -v"`: phase flags and the `ld32` line |
| `smake.object.log` | explicit `hinv.o` compile, same flags, object kept |
| `hinv` | working native n32 binary |
| `hinv.o` | native n32 relocatable object |
| `hinv.output` | stdout of the rebuilt binary |
| `hinv.stock.output` | stdout of the guest's `/sbin/hinv` (identical) |

Rerun with:

```sh
scripts/rig/hinv-reference.sh --tree /home/matt/projects/irix-6.5.7m-src
```

The script takes the rig's shared guest lock around every guest transaction,
cleans `/tmp/hinv-reference` first, and keeps each `iris-ci run` command short
because the guest's serial input silently wedges on long command lines.

Issue #8 will diff the GCC-built `hinv` against `hinv.output` (behaviour) and
`hinv.o`/`hinv` (ELF shape and object layout), with the dry-run and verbose
logs fixing the flags that must be reproduced. The licence banner that MIPSpro
prints (the locally patched driver's known quirk) is expected noise in the
reference logs and is not an error.
