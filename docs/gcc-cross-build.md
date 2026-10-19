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
| GCC-enabling source change | branch of `irix7/irix6` | ADR-0020 |

A clean cross build with **no tree change is a valid outcome**. When the modern
compiler rejects the old source (K&R C, K&R prototypes, implicit int, yacc-era
tools), the fix lands on a branch of `irix7/irix6` — pushed — and the board
item records that branch. Build-side translation (`scripts/runtime/compat.py`)
is an interim path, not the policy.

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
$CC $LDFLAGS $SYSROOT/usr/lib32/crt1.o $SYSROOT/usr/lib32/crtn.o \
    "${OBJS[@]}" -lc -lm -Wl,-I,$SYSROOT/usr/lib32/libc.so.1 -o <binary>
```

The harness refuses a binary without PT_INTERP — a static case is not this
seam (see ADR-0007).

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

**Batch mode (iris ≥ 39d3d62):** `iris-ci batch FILE` (alias `script`) runs
every step over one control-socket connection instead of connect/request/close
per step. A differential test case (put program, put input, run cross, run
reference, get output) collapses from 5 round-trips to 1. Add `--keep-going`
to run past a failed step (the run still exits non-zero).

```sh
eval "$(scripts/rig/task-rig.sh env <item>)"
iris-ci batch --keep-going <<EOF
put $OUT/t1.awk --to /var/tmp/dt/t1.awk
put $OUT/t1.in  --to /var/tmp/dt/t1.in
run "/var/tmp/nawk-fix -f /var/tmp/dt/t1.awk /var/tmp/dt/t1.in >& /var/tmp/dt/t1.gcc; echo IRIS-CI-RC=\$status"
run "/var/tmp/nawk-mipspro/nawk -f /var/tmp/dt/t1.awk /var/tmp/dt/t1.in >& /var/tmp/dt/t1.ref; echo IRIS-CI-RC=\$status"
get /var/tmp/dt/t1.gcc --to $OUT/t1.gcc
get /var/tmp/dt/t1.ref --to $OUT/t1.ref
EOF
diff $OUT/t1.gcc $OUT/t1.ref
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
