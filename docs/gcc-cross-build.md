# GCC cross-build recipe (modernised tree, GCC cc / GCC run)

Rebuild one tree command with the mips-sgi-irix6.5 cross and prove it against
the native reference. Practical companion to `docs/hinv-gcc.md` and ADR-0020.

## Working paths (exact)

| What | Path |
|---|---|
| This repo (build tooling) | `/home/matt/projects/sgi-toolchain-new` |
| IRIX source tree (`irix7/irix6`) | `/home/matt/projects/irix-6.5.7m-src` |
| Rig root (shared volume) | `/mnt/europa/sgi-toolchain-scratch/rig` |
| Emulator binary | `/mnt/europa/sgi-toolchain-scratch/rig/iris/target/release/iris` |
| iris-ci client | `/mnt/europa/sgi-toolchain-scratch/rig/iris/target/release/iris-ci` |
| Task instance root | `/mnt/europa/sgi-toolchain-scratch/rig/tasks/<slug>/` |
| Captured sysroot | `/mnt/europa/sgi-toolchain-scratch/rig/oracle/sysroot` |
| Oracle references | `/mnt/europa/sgi-toolchain-scratch/rig/oracle/<target>-reference/` |
| GCC cross | `/home/matt/projects/sgi-toolchain-new/.scratch/toolchain-16.2.0/prefix/bin/mips-sgi-irix6.5-gcc` |
| Cross gcc wrapper | `/home/matt/projects/sgi-toolchain-new/.scratch/toolchain-16.2.0/prefix/bin/` |

## Environment prerequisites (every rig command)

All rig and cross-build commands run inside the nix rig dev shell:

```sh
nix develop /home/matt/projects/sgi-toolchain-new.#rig -c bash
```

Inside that shell:

```sh
# 1. Put the cross gcc on PATH
export PATH="/home/matt/projects/sgi-toolchain-new/.scratch/toolchain-16.2.0/prefix/bin:$PATH"

# 2. The cross gcc (cc1) needs nix-store isl/gmp/mpfr/mpc at run time.
#    `nix develop .#rig` does NOT export these. Find them with:
nix-store --query --references $(which mips-sgi-irix6.5-gcc) 2>/dev/null | grep -E 'gmp|mpfr|mpc|isl' | head -4
# then set:
export LD_LIBRARY_PATH="/nix/store/<gmp>/lib:/nix/store/<mpfr>/lib:/nix/store/<libmpc>/lib:/nix/store/<isl>/lib:${LD_LIBRARY_PATH:-}"
```

The cross gcc and `ld` are binutils 2.20.1 paired with GCC 16.2 — see the
ld/binutils note below for GCC-run blocking.

## Where the work goes

| stage | artefact | lands |
|---|---|---|
| native reference | `/mnt/europa/sgi-toolchain-scratch/rig/oracle/<target>-reference/` | rig dir, never committed (ADR-0001) |
| cross build evidence | `/home/matt/projects/sgi-toolchain-new/.scratch/<target>-gcc/` | never committed |
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

## The recipe (exact commands)

```sh
cd /home/matt/projects/sgi-toolchain-new

# 0. Start a task instance for this item (base rig must be stopped).
nix develop .#rig -c bash -c '
export IRIX_RIG_DIR=/mnt/europa/sgi-toolchain-scratch/rig
scripts/rig/task-rig.sh start eoe/cmd/awk
eval "$(scripts/rig/task-rig.sh env eoe/cmd/awk)"
scripts/rig/oracle-driver.py --socket "$RIG_SOCKET" --ic "$RIG_IRIS_CI" ensure-shell --quiet
'

# 1. Native reference first (ADR-0005): flags, includes and link step verbatim.
#    --make-target NN when the smake target differs from the binary (awk -> nawk).
scripts/rig/reference-build.sh --tree /home/matt/projects/irix-6.5.7m-src \
    --src-dir eoe/cmd/awk --target nawk --make-target nawk

# 2. Extract the compile and link lines from the captured log.
grep -E '^\s+(cc|/usr/bin/cc)\s' \
    /mnt/europa/sgi-toolchain-scratch/rig/oracle/nawk-reference/smake.dryrun.log

# 3. Stop the instance (native reference captured).
scripts/rig/task-rig.sh stop eoe/cmd/awk
```

Then cross-build on the host (no guest needed):

```sh
cd /home/matt/projects/sgi-toolchain-new
nix develop .#rig -c bash -c '
export PATH="/home/matt/projects/sgi-toolchain-new/.scratch/toolchain-16.2.0/prefix/bin:$PATH"
export LD_LIBRARY_PATH="/nix/store/<gmp>/lib:/nix/store/<mpfr>/lib:/nix/store/<libmpc>/lib:/nix/store/<isl>/lib:${LD_LIBRARY_PATH:-}"
SYSROOT=/mnt/europa/sgi-toolchain-scratch/rig/oracle/sysroot
CC=mips-sgi-irix6.5-gcc

# Compile each object:
$CC -march=mips3 -mabi=n32 -O2 -w -nostdinc -I$SYSROOT/usr/include -c b.c -o b.o

# Link dynamically against the captured startfiles (PT_INTERP + RPATH match reference):
$CC -march=mips3 -mabi=n32 -nostartfiles -nostdlib \
    $SYSROOT/usr/lib32/crt1.o $SYSROOT/usr/lib32/crtn.o \
    b.o lib.o main.o parse.o run.o tran.o awk.lx.o awk.g.o proctab.o \
    -lc -lm -L$SYSROOT/usr/lib32/mips3 -L$SYSROOT/usr/lib32 \
    -Wl,--dynamic-linker,/usr/lib32/libc.so.1 -o nawk
```

`-nostartfiles` is required: the cross GCC driver adds its own default crt1.o
in addition to the explicit sysroot one, causing duplicate `_mcount`/`__start`/
`__istart` definitions.

Link against the resolved `libc.so.1` (the `libc.so → libc.so.1 →
../../lib32/libc.so.1` symlink chain confuses bfd ld). `-march=mips3 -mabi=n32`
must be passed at **link** time as well as compile time — without it,
collect2 defaults to o32 (`-melf32bsmip`) and ld rejects the N32 objects
("File in wrong format").

## Proving it (GCC run)

Start a task instance, ship the cross binary, run it, diff against the oracle:

```sh
nix develop .#rig -c bash -c '
export IRIX_RIG_DIR=/mnt/europa/sgi-toolchain-scratch/rig
scripts/rig/task-rig.sh start eoe/cmd/awk
eval "$(scripts/rig/task-rig.sh env eoe/cmd/awk)"

# Batch mode (iris >= 39d3d62): one control-socket connection for ALL steps.
# iris-ci batch takes a FILE PATH (not stdin/heredoc). ALWAYS pass --socket.
cat > /tmp/awk-t1.batch <<EOF
run "mkdir -p /var/tmp/dt"
run "rm -f /var/tmp/dt/awk-fix /var/tmp/dt/t1.awk /var/tmp/dt/t1.in"
put /tmp/awk-gcc/ev/diffout/t1.awk --to /var/tmp/dt/t1.awk
put /tmp/awk-gcc/ev/diffout/t1.in  --to /var/tmp/dt/t1.in
run "chmod 755 /var/tmp/dt/awk-fix"
run "/var/tmp/awk-fix -f /var/tmp/dt/t1.awk /var/tmp/dt/t1.in >& /var/tmp/dt/t1.gcc; echo IRIS-CI-RC=\$status"
run "/usr/bin/nawk -f /var/tmp/dt/t1.awk /var/tmp/dt/t1.in >& /var/tmp/dt/t1.ref; echo IRIS-CI-RC=\$status"
get /var/tmp/dt/t1.gcc --to /tmp/awk-gcc/ev/diffout/t1.gcc
get /var/tmp/dt/t1.ref --to /tmp/awk-gcc/ev/diffout/t1.ref
EOF
iris-ci batch --socket "$RIG_SOCKET" --keep-going /tmp/awk-t1.batch

diff /tmp/awk-gcc/ev/diffout/t1.gcc /tmp/awk-gcc/ev/diffout/t1.ref
readelf -l /tmp/awk-gcc/nawk | grep INTERP   # PT_INTERP must be present

scripts/rig/task-rig.sh stop eoe/cmd/awk
```

`--keep-going` runs past a failed step (the run still exits non-zero). A
differential test case (put program, put input, run cross, run reference,
get output) collapses from 5 round-trips to 1 with batch mode.

A byte-for-byte match (or, for a command whose only output is a usage message
on stderr, two empty stdout files) is the evidence. Record the diff, the
`readelf -l` PT_INTERP line and the guest stdout in the instance
`work/` directory before advancing `GCC run`.

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

### 2. yacc → bison

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

## Environment friction and workarounds

These are problems agents hit on the rig; they are not source bugs. Add
workarounds here as they recur so each agent starts with them.

### `iris-ci batch` needs `--socket "$RIG_SOCKET"` explicitly

Without it, `iris-ci` falls back to `/tmp/iris.sock` (the sibling session)
and every step fails with `Connection ... NotFound`.

### `put` does not reliably overwrite an existing guest file

After re-staging, `cmp` can report the old content while `md5` of the host
copy matches. Clean first:

```sh
run "rm -f /var/tmp/dt/t1.awk /var/tmp/dt/t1.in"
put $OUT/t1.awk --to /var/tmp/dt/t1.awk
put $OUT/t1.in  --to /var/tmp/dt/t1.in
```

### `put` needs `mkdir -p` for the destination directory

`put` fails if the target directory doesn't exist (e.g. `/var/tmp/dt`).
Create it first:

```sh
run "mkdir -p /var/tmp/dt"
```

### `put` does not preserve the executable bit

Stage scripts with a following `run "chmod 755 ..."` step:

```sh
run "chmod 755 /var/tmp/dt/myscript"
```

### The control socket can disappear for minutes

`iris-ci` reports `connect: No such file or directory` while the emulator
process is alive and spinning. This is stale-cache behaviour on the shared
`/mnt/europa` volume. Wrap every batch call in a retry loop:

```sh
for i in $(seq 1 10); do
  iris-ci batch --socket "$RIG_SOCKET" --keep-going < batch.txt && break
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

### `reference-build.sh --run` aborts on nonzero stock exit

If the stock binary exits nonzero (e.g. `getopt` exits 2 with no args),
`set -e` aborts before capturing output. `--no-run` then aborts `ic get`-ing
the never-created `.output`/`.stock.output` files. Workaround: run the
binaries manually via `iris-ci` and capture the outputs yourself.
