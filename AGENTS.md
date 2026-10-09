# SGI IRIX Toolchain

A toolchain and rebuild project for IRIX 6.5.7m on the SGI Indy: rebuild it privately with IRIX's own tools, then with a modern GCC, then reimplement it in Rust as IRIX 7. Vocabulary lives in `CONTEXT.md`; decisions in `docs/adr/`.

## Agent skills

### Issue tracker

Issues live as GitHub issues on `irix7/project`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Two contexts: this repo's `CONTEXT.md` (toolchain) and the planned `irix7/os`
context; the root `CONTEXT-MAP.md` records the boundary. Decisions in
`docs/adr/`. See `docs/agents/domain.md`.

### Rig and tooling

The guest and its emulator run **outside this repo** under `$IRIX_RIG_DIR`
(default `/mnt/europa/sgi-toolchain-scratch/rig`); each agent gets a private
instance named for its item with `scripts/rig/task-rig.sh start|stop <item>`,
not the shared base rig. The IRIX source tree is the private repo
`irix7/irix6` — source fixes and decompilations both land there, not here.
Entry points and where each result goes: `docs/agents/rebuild-worker.md`; the
rig itself: `docs/rig.md`.

### Status inventory

The rebuild state of every buildable command is tracked in the public GitHub
Project **"IRIX 6.5.7m rebuild"** — `gh project 1 --owner irix7`, or
https://github.com/orgs/irix7/projects/1. Each command is one item, grouped by
the `Subsystem` field with its `.sw.*` `Product` field; the columns are the
single-select fields `src`, `deco`, `IRIX cc`, `IRIX run`, `GCC cc`, `GCC run`,
`Rust` (`done` / `in progress` / `not started` / `blocked` / `n/a`; `src` uses
`full`/`stub`/`none`/`missing`).

Update the board in the same session a status changes — source found, a
component decompiled, compiled (IRIX or GCC), or run on the guest. A later
agent reads the board as ground truth, so never leave it stale.

### Rebuild worker

The parallel rebuild loop is `docs/agents/rebuild-worker.md`: one agent
claims one board item, advances it one field at a time, and the loop forces
the build tooling (`scripts/runtime/smake.py`, `compat.py`, the per-component
drivers) to generalise across the whole tree. Two lanes — recompile
(`src: full`) and reconstruct (`src: stub`/`none`) — each flow `IRIX cc` →
`IRIX run` → `GCC cc` → `GCC run`. A source-absent command joins the
reconstruct lane (decompiled), never parked at `n/a`; a header-only item's job
is to land its headers in the tree for the GCC stage. Tree fixes from either
pass go back into `irix7/irix6`; GCC-enabling changes land on a branch of the
tree (ADR-0020), with build-side translation only as an interim path. The board
helper is `scripts/board.py`
(`frontier` / `status` / `show` / `claim` / `advance` / `set` / `done` /
`release`).
