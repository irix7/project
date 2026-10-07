# Rebuild worker — the parallel stock-rebuild loop

One workflow that parallel agents run to drive every item on the
**IRIX 6.5.7m rebuild** board from source to a proven rebuild. One agent
holds one item at a time and advances it one field at a time. The loop is
deliberately simple; its whole point is to force the build tooling to
generalise across the entire source tree.

## Ground truth

The board is the only source of truth for rebuild state:

- Project: https://github.com/orgs/irix7/projects/1 (`irix7/projects/1`,
  node id `PVT_kwDOFBsSF84BmAM2`).
- ~440 buildable draft items (377 `full`, 55 `stub`, 10 `none` at time of
  writing) — the full IRIX 6.5.7m product set, plus the binary-only kernel
  and userland modules that need reconstruction.
- Fields: `src` / `deco` / `IRIX cc` / `IRIX run` / `GCC cc` / `GCC run` /
  `Rust`, plus the shared `Status` (Todo / In Progress / Done).
- A later agent reads the board as ground truth. Never leave it stale: every
  transition is recorded in the same session it happens (AGENTS.md).

`scripts/board.py` is the one interface to the board. It resolves field and
option ids by name at run time and never hard-codes them, so it stays correct
if the project is re-created.

## The two lanes and the per-item flow

- **Recompile lane** (`src: full`): `IRIX cc` → `IRIX run` → `GCC cc` →
  `GCC run`.
- **Reconstruct lane** (`src: stub` or `none`): `deco` (decompile to C)
  first, then the recompile lane.
- **`src: missing`**: not buildable in this stage. Mark `IRIX cc`/`IRIX run`
  `n/a` and open a source-restoration issue; the item does not gate the
  other lanes.

Each item flows **sequentially**; the board as a whole runs **in parallel**.
One agent can be doing `IRIX cc` on item A while another does `GCC cc` on
item B whose native reference is already recorded. The only ordering is
per-item, and it is real: reference-first (ADR-0005) means an item's GCC step
needs *that item's own* native reference and oracle output to diff against.

## Stage 1 — native build with IRIX tools (`IRIX cc` / `IRIX run`)

Build the item with the tree's own tools inside the guest: MIPSpro `cc`,
`smake`, the same pattern as `scripts/rig/hinv-reference.sh` and
`scripts/rig/oracle.sh` (see `docs/reference-hinv.md`, `docs/oracle.md`).

The defining rule of this stage: **when the source tree needs a fix — a
broken makefile, a missing file, a release erratum — the fix goes back into
the private tree repo `irix7/irix6`, not into a build-side workaround.** The
native pass exists to make the tree itself buildable, and each fix lands
once, centrally, instead of scattering per-component shims through the build
scripts. `docs/inventory.md`'s "Source-restoration fixes" are the model:
the smake `$(VAR)SUFFIX=` idiom across 248 Makefiles, the sash `-coff` → ELF
path, the libsk/libsc/libsl archive rule, and the header farm.

- `IRIX cc` = `done` when the item builds natively with IRIX tools.
- `IRIX run` = `done` when the native binary runs in the guest and its
  stdout is captured as the oracle — the ground truth stage 2 diffs against.

## Stage 2 — GCC cross-compile (`GCC cc` / `GCC run`)

Once an item's native reference exists, build the same (now-fixed) tree with
the GCC 16.2 cross (`mips-sgi-irix6.5`).

MIPSpro-only constructs that the tree uses *legitimately* are translated at
the tooling layer — `scripts/runtime/compat.py`, `scripts/runtime/smake.py`,
the flag-translation table (`docs/hinv-gcc.md`) — **never by editing the
tree**. The tree stays stock for toolchain reasons; only genuine defects
change it, and those landed in stage 1.

- `GCC cc` = `done` when the cross build succeeds.
- `GCC run` = `done` when the cross binary runs in the guest and its stdout
  matches the oracle byte-for-byte (`scripts/smoke.sh` /
  `scripts/lib/guest-txn.sh`, fail-closed; ELF-shape proof via the cross's
  `readelf`).

`Rust` is stage-three (the modernised fork) work. When an item reaches it,
mark it `n/a` for now; it is not part of the stock rebuild.

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
| `scripts/runtime/compat.py` | translates libc's MIPSpro constructs | translates any source |
| `scripts/rig/hinv-gcc.sh`, `scripts/runtime/rebuild-libc.py` | one command, one library | a general per-component driver |
| header farm (`scripts/runtime/include/`, the `irix/usr/include` supplement) | libc + hinv headers | every header the tree needs |

This is the point of the parallel run: **each item's rebuild extends the
substrate, with a test, so later items need less new tooling, not more.** Any
new smake idiom, MIPSpro construct, missing header or flag translation found
while rebuilding any item lands in the shared tooling. The substrate
converges on the whole tree as the board is worked.

## The loop, concretely

```sh
scripts/board.py frontier                 # pick an unclaimed item
scripts/board.py claim eoe.sw.base        # Status -> In Progress, IRIX cc -> in progress
# ... do the one transition's work ...
scripts/board.py advance eoe.sw.base      # IRIX cc -> done, Status -> Todo
```

For a non-applicable field, or a blocked one, use the raw setter:

```sh
scripts/board.py set eoe.sw.base "IRIX run" n/a
scripts/board.py set gfx_framework.o deco blocked
```

Then:

- **Tree fixes** → commit to `irix7/irix6`.
- **Tooling extensions** → commit here after the publication gate.
- **Evidence** (objects, binaries, logs, guest output) → `.scratch/` only,
  never committed (ADR-0001).

## Command cheat-sheet

```sh
scripts/board.py status                   # board-wide remaining-work summary
scripts/board.py frontier [--lane full|stub|none|missing]
scripts/board.py show <item>              # one item's full field state + next action
scripts/board.py claim <item>             # take it: Status -> In Progress, next field -> in progress
scripts/board.py advance <item> [--value done|n/a]   # finish the in-progress step, hand back
scripts/board.py set <item> <field> <value>          # raw single-select setter
scripts/board.py done <item>              # Status -> Done (only after GCC run)
scripts/board.py release <item>           # abandon a claim
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
- The guest is a single shared resource. Every guest access goes through the
  rig's one shared lock (`scripts/lib/guest-txn.sh`, `docs/rig.md`); do not
  wrap a whole script in that lock.
- MIPSpro defaults to n32 on this guest and `-S` ignores `-o`; name the ABI
  explicitly and expect new binaries to need `rehash` in the guest's csh
  (`docs/oracle.md`).
- A blocked item is a progress report, not a completion: set the field
  `blocked`, record the blocker, and hand the item back — never mark a field
  `done` because a build merely ran or a blocker was documented (the audit
  evidence policy).
