# GCC cross-build recipe (modernised tree, GCC cc / GCC run)

How to rebuild one tree command with the mips-sgi-irix6.5 cross and prove it
against the native reference. This is the practical companion to
`docs/hinv-gcc.md` (which documents the hinv-specific flag translation) and
to ADR-0020, which says where the source change goes.

## Where the work goes

| stage | artefact | lands |
|---|---|---|
| native reference | `$RIG_ORACLE_DIR/<target>-reference/` | rig dir, never committed (ADR-0001) |
| cross build evidence | `<repo>/.scratch/<target>-gcc/` | never committed |
| GCC-enabling source change | branch of `irix7/irix6`, PR'd into `modern-c` | ADR-0020 |

A clean cross build with **no tree change is a valid outcome**. When the modern
compiler rejects the old source (K&R C, K&R prototypes, implicit int, yacc-era
tools), the fix lands on a branch of `irix7/irix6` — pushed — and PR'd into
`modern-c` so each item picks up previous conversions. Build-side translation
(`scripts/runtime/compat.py`) is an interim path, not the policy.

**Branch off `modern-c`, not `main`:** each fix branch should be created from
`irix6/modern-c` so it includes all prior ANSI conversions and toolchain fixes.
When the fix is green, PR it back into `modern-c` (not `main`). `modern-c` is
the integrating branch; `main` stays as the untouched stock tree.

## The recipe

```sh
# 1. Native reference first (ADR-0005): flags, includes and link step verbatim.
scripts/rig/reference-build.sh --tree /home/matt/projects/irix-6.5.7m-src \
    --src-dir eoe/cmd/awk --target nawk        # --make-target NN if it differs

# 2. Extract the compile and link lines from the captured log.
grep -E '^\s+(cc|/usr/bin/cc)\s' \
    $RIG_ORACLE_DIR/nawk-reference/smake.dryrun.log

# 3. Cross-build, using the reference lines' flags.
```

### Flag translation (from the MIPSpro line to GCC)

| MIPSpro | GCC | note |
|---|---|---|
| `-mips3 -n32` | `-march=mips3 -mabi=n32` | |
| `-nostdinc -I//usr/include` | `-nostdinc -I$SYSROOT/usr/include` | |
| `-O` | `-O2` | |
| `-non_shared` / `-L//usr/lib32/nonshared` | drop | the guest ships no non-shared crt; link dynamically (ADR-0006) |
| `-nostdlib -L//usr/lib32` | `-nostdlib -L$SYSROOT/usr/lib32` | |
| `-lw -lm` | `-lc -lm` | `libw` is absent from the captured sysroot; its symbols live in libc |
| `-MDupdate Makedepend`, `-woff …`, `-v`, `-cckr` | drop | driver and warning-control only |

Link explicitly against the captured startfiles so PT_INTERP and RPATH match
the reference (ADR-0006):

```sh
$CC $LDFLAGS -nostartfiles $SYSROOT/usr/lib32/crt1.o $SYSROOT/usr/lib32/crtn.o \
    "${OBJS[@]}" -lc -lm -Wl,-I,$SYSROOT/usr/lib32/libc.so.1 -o <binary>
```

`-nostartfiles` is required: the cross GCC driver adds its own default
crt1.o in addition to the explicit sysroot one, causing duplicate
`_mcount`/`__start`/`__istart` definitions.

## The three things that actually bite

### 1. K&R C

MIPSpro `cc -cckr` accepts K&R definitions; GCC 16 does not. Every item that
built natively may still fail the cross build on:

```c
penter(p)                    ->  penter(Node *p)
Node *p;                     ->
{                            ->  {
    ...
}
double log(), sqrt();        ->  double log(double), sqrt(double);
```

`eoe/cmd/awk` needed all of `b.c lib.c main.c parse.c run.c tran.c` converted
(branch `fix/awk-ansi-c` on `irix7/irix6`, commit `39aac27b`) before the cross
compiler would even surface its type errors. Convert signatures against the
call sites, not by guesswork: `awk.h`'s `double log(), sqrt(), exp(), atof();`
conflicts with the sysroot's ANSI headers until each arity/type is fixed.

Implicit-int return (`void foo()`) is accepted with a warning under `-w`; only
the definitions need converting.

### 2. The cross-compiler needs nix libraries at run time

`cc1` links against the nix-store isl/gmp/mpfr/mpc and fails with
`error while loading shared libraries` unless they are on the path:

```sh
export LD_LIBRARY_PATH="$GMP/lib:$MPFR/lib:$MPC/lib:$ISL/lib:$LD_LIBRARY_PATH"
```

Run everything inside `nix develop .#rig` (bison and flex live there too), and
pass `--keep IRIX_RIG_DIR` if a command goes through `nix develop`.

### 3. yacc → bison

The guest's `yacc` is gone; bison 3.8 emits an **enum**, not the `#define`
tokens `maketab`-era tools parse:

```
    PROGRAM = 259,          /* PROGRAM  */
```

Any build-time table generator reading `#define TOKEN 259` from `y.tab.h` must
accept both the classic `#define` form and the enum form (see `maketab.c` in
`eoe/cmd/awk`). Generate with `bison -d -o y.tab.c awk.g.y` **and then `cp
y.tab.c <the name the Makefile expects>`** — some Makefiles `#include
"y.tab.h"`, which bison still writes, so `#include "y.tab.h"` works as-is.

flex is a drop-in for the tree's `lex` calls (`flex -o lex.yy.c awk.lx.l`).

## Proving it (GCC run)

Ship the cross binary to a task instance and diff against the reference
output; the reference captured both the rebuilt and the stock binary's stdout
under `$RIG_ORACLE_DIR/<target>-reference/`.

**Batch mode (iris ≥ 39d3d62):** `iris-ci batch --keep-going <PATH>` (alias
`script`) runs every step over ONE control-socket connection instead of
connect/request/close per step. The CLI takes a **file path** (not stdin/heredoc).
A differential test case (put program, put input, run cross, run reference,
get output) collapses from 5 round-trips to 1. `--keep-going` runs past a
failed step (the run still exits non-zero).

Write one batch file per test case:

```sh
cat > $OUT/t1.batch <<EOF
put $OUT/t1.awk --to /var/tmp/dt/t1.awk
put $OUT/t1.in  --to /var/tmp/dt/t1.in
run "/var/tmp/nawk-fix -f /var/tmp/dt/t1.awk /var/tmp/dt/t1.in >& /var/tmp/dt/t1.gcc; echo IRIS-CI-RC=\$status"
run "/var/tmp/nawk-mipspro/nawk -f /var/tmp/dt/t1.awk /var/tmp/dt/t1.in >& /var/tmp/dt/t1.ref; echo IRIS-CI-RC=\$status"
get /var/tmp/dt/t1.gcc --to $OUT/t1.gcc
get /var/tmp/dt/t1.ref --to $OUT/t1.ref
EOF
iris-ci batch --keep-going $OUT/t1.batch
diff $OUT/t1.gcc $OUT/t1.ref
```

**`-nostartfiles`:** the cross GCC driver adds its own default crt1.o in
addition to the explicit sysroot one, causing duplicate `_mcount`/`__start`/
`__istart` definitions. Pass `-nostartfiles` when linking against the
captured sysroot:

```sh
$CC $LDFLAGS -nostartfiles $SYSROOT/usr/lib32/crt1.o $SYSROOT/usr/lib32/crtn.o \
    <objs> -lc -lm -Wl,-I,$SYSROOT/usr/lib32/libc.so.1 -o <binary>
```


```sh
eval "$(scripts/rig/task-rig.sh env <item>)"
# put, run, get, then:
diff $RIG_ORACLE_DIR/<target>-reference/<target>.output <output-from-guest>
```

A byte-for-byte match (or, for a command whose only output is a usage message
on stderr, two empty stdout files) is the evidence. Record the diff, the
`readelf -l` PT_INTERP line and the guest stdout in the instance `work/`
directory before advancing `GCC run`.

## Environment friction and workarounds

These are problems agents hit on the rig; they are not source bugs. Add
workarounds here as they recur so each agent starts with them.

### `put` does not reliably overwrite an existing guest file
After re-staging, `cmp` can report the old content while `md5` of the host
copy matches. Clean first:

```sh
iris-ci batch <<EOF
run "rm -f /var/tmp/dt/t1.awk /var/tmp/dt/t1.in"
put $OUT/t1.awk --to /var/tmp/dt/t1.awk
put $OUT/t1.in  --to /var/tmp/dt/t1.in
EOF
```

### The control socket can disappear for minutes
`iris-ci` reports `connect: No such file or directory` while the emulator
process is alive and spinning. This is stale-cache behaviour on the shared
`/mnt/europa` volume. Wrap every batch call in a retry loop:

```sh
for i in $(seq 1 10); do
  iris-ci batch --keep-going < batch.txt && break
  sleep 20
done
```

### `iris-ci batch` cannot quote multi-word commands cleanly
It splits lines on whitespace and honours quotes but not `--`, so
`run -- mkdir -p /x` fails. Always quote multi-word guest commands:

```sh
run "chmod 755 /var/tmp/dt/myscript"
run "/var/tmp/dt/myscript arg1 arg2"
```

`put` also does **not** preserve the executable bit — stage scripts with a
following `run "chmod 755 ..."` step.

### Guest hangs block `iris-ci run` for its full timeout
When a guest binary hangs, `iris-ci run` blocks until its `--timeout`
(default 60 s) and Ctrl-C never reaches the shell. The only recovery is:

```sh
scripts/rig/task-rig.sh stop <item>
scripts/rig/task-rig.sh start <item>
eval "$(scripts/rig/task-rig.sh env <item>)"
python3 scripts/rig/oracle-driver.py --socket "$RIG_SOCKET" --ic "$RIG_IRIS_CI" ensure-shell --quiet
```

### `>& file` fails inside `sh -c`
`iris-ci run 'sh -c "cmd > out >& err"'` reports "bad file unit number".
Use a direct `run` line instead: `( cmd > out ) >& err`.

### Unknown source suffixes
`gcc -c x.c.debug` treats `.debug` as a linker input, not a C source.
Use `-x c` for any debug copy of a source file.

### Cross compiler runtime environment
`nix develop .#rig` does **not** export GMP/MPFR/MPC/ISL paths and does
**not** place the cross gcc on PATH. You must supply both manually:

```sh
export LD_LIBRARY_PATH="/nix/store/...-gmp-*/lib:/nix/store/...-mpfr-*/lib:/nix/store/...-libmpc-*/lib:/nix/store/...-isl-*/lib:${LD_LIBRARY_PATH:-}"
export PATH="/home/matt/projects/sgi-toolchain-new/.scratch/toolchain-16.2.0/prefix/bin:$PATH"
mips-sgi-irix6.5-gcc ...
```

### csh guest shell
The guest shell is csh. Host bash expands `$status` (unbound variable),
so wrap guest command strings in **single quotes** or escape as `\$status`.
`2>&1`/`2>` are invalid in csh — use `>` or `>&` only. One simple command
per `iris-ci run`.

### ld rejects the `libc.so` symlink chain
The sysroot's `libc.so → libc.so.1 → ../../lib32/libc.so.1` chain confuses
bfd ld ("File in wrong format"). Link against the resolved
`libc.so.1` instead:

```sh
$CC $LDFLAGS -nostartfiles -nostdlib $SYSROOT/usr/lib32/crt1.o \
    $SYSROOT/usr/lib32/crtn.o <objs> -lc -lm \
    -Wl,-I,$SYSROOT/usr/lib32/libc.so.1 -o <binary>
```

### `-march`/`-mabi` at link time too
`-march=mips3 -mabi=n32` must be passed at **link** time as well as compile
time — without it, collect2 defaults to o32 (`-melf32bsmip`) and ld rejects
the N32 objects ("File in wrong format").

### `reference-build.sh --run` aborts on nonzero stock exit
If the stock binary exits nonzero (e.g. `getopt` exits 2 with no args),
`set -e` aborts before capturing output. `--no-run` then aborts `ic get`-ing
the never-created `.output`/`.stock.output` files. Workaround: run the
binaries manually via `iris-ci` and capture the outputs yourself.

### `put` needs `mkdir -p` for the destination directory
`put` fails if the target directory doesn't exist (e.g. `/var/tmp/dt`).
Create it first:

```sh
run "mkdir -p /var/tmp/dt"
```

### ld/binutils mismatch: item-dependent, not universal
`mips-sgi-irix6.5-ld` is binutils 2.20.1 paired with GCC 16.2. ld 2.20.1
can emit a single **RWE** LOAD segment for N32 MIPS, and some items (e.g.
`cal`) link and run cleanly despite this. Others (e.g. `pwd`, `getopt`)
segfault at startup — attempts to force separate segments (custom linker
script, `--max-page-size`, `-z separate-code`, `-n`/`-N`) make ld 2.20.1
segfault. `gas` 2.20 also cannot emit a valid `MIPS_REGINFO` section, and
the sysroot's MIPSpro `crt1.o` is runtime-broken with GCC's ld gp layout.

**GCC run is item-dependent:** advance to `blocked` only when the cross
binary actually segfaults on the guest. Otherwise mark `done`. The fix is
infrastructure (rebuild the cross toolchain against binutils 2.47,
`irix7/binutils-gdb:irix7/irix-binutils-2.47`), not per-item source.

### MIPSpro cc licence
The guest may carry only a WorkShop eval FlexLM feature, so native `cc`
refuses to compile phases. Fix with `scripts/rig/oracle.sh patch` using a
locally prepared 7.3 driver with the licence check removed (see
`docs/oracle.md`). Neither the driver nor the licence is ever committed.

### Missing guest headers
The rig base image may lack headers the dev install doesn't ship (e.g.
`/usr/include/msgs/uxsgicore.h`). Provision them on the guest **and** add
them to the host sysroot so the cross build finds them.

### `gettxt` fallback quirk
This IRIX libc returns the literal `"Message not found!!"` for bare numeric
msgids; it only falls back to the default string for `"catalog:message"`
form (e.g. `uxsgicore:N`). Provision headers using the catalog form so
`gettxt` returns English defaults.

### `lib.sh` re-enables `set -euo pipefail`
Sourcing `lib.sh` re-enables `set -euo pipefail`, which breaks inline
`$IRIS_SOCKET` checks (unbound variable). Be defensive when composing rig
commands.


