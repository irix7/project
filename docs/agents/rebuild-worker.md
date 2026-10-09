# Rebuild worker — the parallel rebuild loop

One workflow that parallel agents run to drive every item on the
**IRIX 6.5.7m rebuild** board from source to a proven rebuild. One agent
holds one item at a time and **carries it through its lane**, recording each
field transition. The loop is deliberately simple; its whole point is to force
the build tooling to generalise across the entire source tree.

## Worker brief — read this first

This is the accumulated context for every worker; it grows as the loop learns.

- **The board drives the loop.** `scripts/board.py claim`/`next`/`advance`/`show`
  print the briefing for the stage you are about to do, and the board protocol.
  Follow it; it is kept current here (`STEP_GUIDE` in `board.py`).
- **Carry the item through its lane.** Do `IRIX cc` → `IRIX run` → `GCC cc` →
  `GCC run` as far as you can in one session, recording each transition. Do not
  stop after the native build when the cross build is next.
- **Classify before you boot** (see "The two lanes" below). Never spend a guest
  boot on an item that has nothing to build.
- **The guest starts paused.** `task-rig.sh start` brings up the emulator but
  the CPU is idle and the serial log stays empty until you drive it: start the
  CPU and log in through `iris-ci` (the `ensure-shell` action of
  `scripts/rig/oracle-driver.py` does this). Waiting on the serial log alone
  waits forever.
- **Address your instance with `env`/`run`, never hand-set root paths.**
  `eval "$(scripts/rig/task-rig.sh env <item>)"` points the shared scripts at
  your instance (by default they address the base rig);
  `scripts/rig/task-rig.sh run <item> -- <cmd>...` does it for one command.
  A cap of `IRIX_TASK_MAX` (default 2) running instances is enforced because
  each emulator pins a core.
- **The guest shell is csh.** Use `>&`/`>>&`, not `2>&1` (csh rejects it with
  "Ambiguous output redirect"), and `$status`, not `$?`. Send **one simple
  command per `iris-ci run`**: a csh syntax error aborts the line before the
  `IRIS-CI-RC=` sentinel, and the client then waits the full timeout.
- **Board protocol.** `advance` completes the one in-progress field and flips
  the item back to `Todo`. For the next field, do not just `advance` again:
  `scripts/board.py advance <item> --field "<FIELD>" --value done` (or `claim`
  then `advance`). Set a field `done` only with evidence; a field that could
  not be attempted is `blocked` or `n/a`, never `done`.
- **Evidence** lives in the instance's `work/` directory, never committed.
- **Report friction.** Name every place the docs, scripts or rig were wrong,
  ambiguous or made you do busywork, with the exact command. This is the loop's
  second output: the next worker's context is improved from it.

## Ground truth

The board is the only source of truth for rebuild state:

- Project: https://github.com/orgs/irix7/projects/1 (`irix7/projects/1`,
  node id `PVT_kwDOFBsSF84BmAM2`).
- One board item per buildable command, flattened out of the IRIX 6.5.7m
  product set (plus the binary-only kernel and userland modules that need
  reconstruction). Counts are live: ask `scripts/board.py status`, never trust
  a number written here.
- Fields: `src` / `deco` / `IRIX cc` / `IRIX run` / `GCC cc` / `GCC run` /
  `Rust`, plus the grouping fields `Subsystem` / `Product` and the shared
  `Status` (Todo / In Progress / Done).
- A later agent reads the board as ground truth. Never leave it stale: every
  transition is recorded in the same session it happens (AGENTS.md).

`scripts/board.py` is the one interface to the board. It resolves field and
option ids by name at run time and never hard-codes them, so it stays correct
if the project is re-created.

## Where the work lives

The three locations:

- **The rig and guest** run **outside this repo**, under `$IRIX_RIG_DIR`
  (default `/mnt/europa/sgi-toolchain-scratch/rig`). Each agent brings up its
  own instance, named for the item, with `scripts/rig/task-rig.sh start
  <item>` — its own socket, lock, overlay and work dir — and stops it with
  `scripts/rig/task-rig.sh stop <item> [--rm]`. See `docs/rig.md`,
  "Per-task instances". Never search this repo for the emulator; a missing one
  means run `scripts/rig/build-iris.sh`.
- **The source tree** is the private repo `irix7/irix6`, worked in at
  `/home/matt/projects/irix-6.5.7m-src` (override with `IRIX_SRC_TREE`).
  Branch `main`; three roots — `irix/` (kernel, libs, core commands), `eoe/`
  (userland product set), `stand/` (boot/PROM). It has two remotes: `origin`
  is the upstream customer tree, `irix6` is `irix7/irix6`.
- **The build tooling** lives in `scripts/` of *this* repo and is published
  here, after the publication gate.

Every result lands somewhere concrete:

- **Source fixes** → commit in the tree working copy and push to its `irix6`
  remote. The tree is the single source of truth, never a build-side shim.
- **Decompilations** (reconstruct lane) → `decompiled/` in the same tree repo,
  beside the stubs the customer tree ships (`ng1stubs`, `gfxstubs`, …); layout
  and the 6.5.7m-overlay vintage policy are in its `decompiled/README.md`.
- **Tooling extensions** → this repo, after `docs/publication.md`.
- **Evidence** (objects, binaries, logs, guest output) → the instance's
  `$IRIX_RIG_DIR/tasks/<item>/work/`, on the shared volume, never committed
  (ADR-0001).

## The two lanes and the per-item flow

**Classify the item before you touch the rig** — the classification picks the
lane, and decides whether a guest is needed at all. Most wasted work comes from
booting a guest for an item that has nothing to build.

- **Buildable command, source present** (`src: full`) — **recompile lane**:
  `IRIX cc` → `IRIX run` → `GCC cc` → `GCC run`.
- **Buildable command, source absent** (`src: stub`/`none`) — **reconstruct
  lane**: `deco` (decompile the shipped binary to C) first, then the recompile
  lane. An item whose source is gone is **decompiled**, not abandoned.
- **`src: missing`** — the source has not been *located* yet. Search the tree
  and the dist media first; if only the binary shipped, the item is `none`:
  set it and join the reconstruct lane. Do not dead-end at `n/a` merely because
  the source is absent.
- **Header or install-only product, no command** — **headers and data**: the
  deliverable is that the product's files are present in the private tree
  (headers, include fragments, rule databases), because they are inputs to the
  modernised-tree GCC build and the legacy-ABI sysroot. Confirm each file is
  there, recover any missing ones from the media, record the evidence, then set
  the compile columns `n/a`. Never boot a guest to "compile" a header.
- **Genuinely unrecoverable** (no source, no binary, no media) — open a
  source-restoration issue; the item gates nothing.

Each item flows **sequentially**; the board as a whole runs **in parallel**.
One agent can be doing `IRIX cc` on item A while another does `GCC cc` on
item B whose native reference is already recorded. The only ordering is
per-item, and it is real: reference-first (ADR-0005) means an item's GCC step
needs *that item's own* native reference and oracle output to diff against.

## Stage 1 — stock rebuild with IRIX tools (`IRIX cc` / `IRIX run`)

Build the item with the tree's own tools inside the guest: MIPSpro `cc`,
`smake`, the same pattern as `scripts/rig/hinv-reference.sh` and
`scripts/rig/oracle.sh` (see `docs/reference-hinv.md`, `docs/oracle.md`).

**Locate the source first.** The board's `src` field reports availability in
the *full* IRIX tree (`irix7/irix6`) and the dist media, **not** what a local
checkout happens to contain — a `src: full` item may still have no source in
a partial checkout. The tree has three source roots, and the item's title
tells you which one to look in:

- `irix/` — kernel, system libraries, core commands: `irix/kern/...`,
  `irix/lib/...`, `irix/cmd/...`.
- `eoe/` — the userland product set (the `.sw.*` items): `eoe/cmd/<name>`,
  `eoe/lib/...`, `eoe/include/...`. `eoe.sw.base` is here, not under `irix/`.
- `stand/` — the standalone boot and PROM programs.

The first act of `IRIX cc` is to find the item's sources and makefiles under
the right root. If they are genuinely absent, recover them into `irix7/irix6`
from the media, or set `src: missing` and open a source-restoration issue
rather than building from nothing.

The defining rule of this stage: **when the source tree needs a fix — a
broken makefile, a missing file, a release erratum — the fix goes back into
the private tree repo `irix7/irix6`, not into a build-side workaround.** The
native pass exists to make the tree itself buildable, and each fix lands
once, centrally, instead of scattering per-component shims through the build
scripts. The model fixes so far: the smake `$(VAR)SUFFIX=` idiom across 248
Makefiles, the sash `-coff` → ELF path, the libsk/libsc/libsl archive rule,
and the header farm.

- `IRIX cc` = `done` when the item builds natively with IRIX tools.
- `IRIX run` = `done` when the native binary runs in the guest and its
  stdout is captured as the oracle — the ground truth stage 2 diffs against.

## Stage 2 — modernised tree (`GCC cc` / `GCC run`)

Once an item's stock rebuild exists, build the same tree with the GCC cross
(`mips-sgi-irix6.5`). The old IRIX sources do not satisfy a modern compiler, so
this stage modernises the tree itself: GCC-enabling change — old-C constructs,
makefile idioms, flags — lands on a branch of the private source tree
`irix7/irix6`. The stream-translation tooling (`compat.py`, the flag table) is
an interim path, not the direction (ADR-0020). The change stays build-enabling:
no operating-system behaviour changes.

- `GCC cc` = `done` when the cross build succeeds.
- `GCC run` = `done` when the cross binary runs in the guest and its stdout
  matches the oracle byte-for-byte (`scripts/smoke.sh` /
  `scripts/lib/guest-txn.sh`, fail-closed; ELF-shape proof via the cross's
  `readelf`).

`Rust` is the IRIX 7 stage. When an item reaches it, mark it `n/a` for now; it
is not part of the stock rebuild or the modernised tree.

## The three disciplines the loop enforces

1. **Publication gate** — before any commit, nothing SGI-derived enters the
   repo (ADR-0001). Run the digest inventory in `docs/publication.md`; it
   refuses tree-text leakage. `compat.py` and `smake.py` match by syntax and
   digest, never by carrying source excerpts. The audit-212013a libc leak is
   the standing cautionary tale.
2. **Substrate serialisation** — one writer of shared tooling at a time
   (`smake.py`, `compat.py`, the general driver, the header farm). Two agents
   must not edit the same tooling file in the same session.
3. **Regression test required** — every tooling extension ships a host-only
   unit test (`test-smake.py`, `test-compat.py`, and the others) before it
   lands. No one-off, per-component hacks in the build scripts.

## Forcing the tooling to generalise

The shared substrate today is libc- and hinv-shaped, not general:

| Tooling | Scope today | Must become |
| --- | --- | --- |
| `scripts/runtime/smake.py` | evaluates libc's leaf makefiles | evaluates any leaf makefile |
| `scripts/runtime/compat.py` | translates libc's MIPSpro constructs | interim; re-homed into the modernised tree branch (ADR-0020) |
| `scripts/rig/hinv-gcc.sh`, `scripts/runtime/rebuild-libc.py` | one command, one library | a general per-component driver |
| header farm (`scripts/runtime/include/`, the `irix/usr/include` supplement) | libc + hinv headers | every header the tree needs |

This is the point of the parallel run: **each item's rebuild extends the
substrate, with a test, so later items need less new tooling, not more.** Any
new smake idiom, missing header or flag translation found while rebuilding any
item extends the shared tooling; MIPSpro-era source constructs are modernised
on the tree branch (ADR-0020). The substrate converges on the whole tree as the
board is worked.

Until a general per-component driver exists, the worker hand-drives the
boot/login/stage/build/pull cycle (modelled on `scripts/rig/hinv-reference.sh`)
and **records the exact commands in its instance's `work/commands.txt`** so the
sequence can be promoted into `scripts/rig/<component>-reference.sh` with a host
test in a later, serialised substrate pass. Do not land shared-tooling changes
opportunistically from a single-item session (discipline 2).

## The loop, concretely

```sh
scripts/board.py next                       # allocate a random claimable item
scripts/rig/task-rig.sh start eoe.sw.base   # private emulator (tasks/eoe.sw.base/work)
# ... do the one transition's work ...
scripts/rig/task-rig.sh stop eoe.sw.base    # stop (--rm discards the guest)
scripts/board.py advance eoe.sw.base        # IRIX cc -> done, Status -> Todo
```

For a non-applicable field, or a blocked one, use the raw setter:

```sh
scripts/board.py set eoe.sw.base "IRIX run" n/a
scripts/board.py set gfx_framework.o deco blocked
```

Then land the result as in **Where the work lives** (tree fix → `irix6`,
decompilation → `decompiled/`, tooling → this repo, evidence → the instance's
`work/`).

## Command cheat-sheet

```sh
scripts/board.py status                   # board-wide remaining-work summary
scripts/board.py frontier [--lane full|stub|none|missing]
scripts/board.py next [--lane full|stub|none|missing]   # allocate a random claimable item
scripts/board.py show <item>              # one item's full field state + next action
scripts/board.py claim <item>             # take it: Status -> In Progress, next field -> in progress
scripts/board.py advance <item> [--value done|n/a]   # finish the in-progress step, hand back
scripts/board.py set <item> <field> <value>          # raw single-select setter
scripts/board.py done <item>              # Status -> Done (only after GCC run)
scripts/board.py release <item>           # abandon a claim

scripts/rig/task-rig.sh start <item> [--gui]   # bring up this item's emulator
scripts/rig/task-rig.sh stop  <item> [--rm]    # stop it (--rm discards the overlay)
scripts/rig/task-rig.sh list                   # every known instance
```

`claim`, `advance`, `set`, `done` and `release` accept `--dry-run` to print
the underlying `gh project item-edit` command without executing it.

The existing per-item tooling is reached the same way as before:

```sh
scripts/rig/hinv-reference.sh --tree /home/matt/projects/irix-6.5.7m-src   # native reference
scripts/rig/hinv-gcc.sh --tree /home/matt/projects/irix-6.5.7m-src         # cross build + diff
python3 scripts/runtime/rebuild-libc.py --tree /home/matt/projects/irix-6.5.7m-src
```

## Gotchas

- The board's fields are inconsistently initialised: some are `not started`,
  some unset. Treat unset as `not started` and always set fields explicitly
  when advancing (`board.py` does).
- GitHub's GraphQL API has a point budget; a board write can fail with a
  rate-limit error even though the transition is valid. Wait for the reset the
  error names and retry — never treat the transition as failed or skip
  recording it.
- Each agent's emulator is its own `task-rig.sh` instance with its own guest
  lock, so parallel workers do not serialise on the guest. The **base** rig is
  the one shared resource: its installed disk is every instance's read-only
  base, so stop it before starting tasks (see `docs/rig.md`). Instances seed
  their NVRAM from the base on creation, so the PROM keeps its
  `SystemPartition`/`console=d` environment instead of dropping to the
  maintenance menu.
- MIPSpro defaults to n32 on this guest and `-S` ignores `-o`; name the ABI
  explicitly and expect new binaries to need `rehash` in the guest's csh
  (`docs/oracle.md`).
- A blocked item is a progress report, not a completion: set the field
  `blocked`, record the blocker, and hand the item back — never mark a field
  `done` because a build merely ran or a blocker was documented (the audit
  evidence policy).
