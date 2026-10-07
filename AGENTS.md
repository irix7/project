# SGI IRIX Toolchain

A toolchain and rebuild project for IRIX 6.5.7m on the SGI Indy: reproduce the operating system privately with GCC 16.2, then modernise it. Vocabulary lives in `CONTEXT.md`; decisions in `docs/adr/`.

## Agent skills

### Issue tracker

Issues live as GitHub issues on `irix7/project`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` at the root, decisions in `docs/adr/`. See `docs/agents/domain.md`.

### Status inventory

The rebuild state of every component is tracked in the public GitHub Project
**"IRIX 6.5.7m rebuild"** — `gh project 1 --owner irix7`, or
https://github.com/orgs/irix7/projects/1. Each component is one item; the
columns are the single-select fields `src`, `deco`, `IRIX cc`, `IRIX run`,
`GCC cc`, `GCC run`, `Rust` (`done` / `in progress` / `not started` / `blocked`
/ `n/a`; `src` uses `full`/`stub`/`none`/`missing`).

Update the board in the same session a status changes — source found, a
component decompiled, compiled (IRIX or GCC), or run on the guest. A later
agent reads the board as ground truth, so never leave it stale.

### Rebuild worker

The parallel stock-rebuild loop is `docs/agents/rebuild-worker.md`: one agent
claims one board item, advances it one field at a time, and the loop forces
the build tooling (`scripts/runtime/smake.py`, `compat.py`, the per-component
drivers) to generalise across the whole tree. Two lanes — recompile
(`src: full`) and reconstruct (`src: stub`/`none`) — each flow `IRIX cc` →
`IRIX run` → `GCC cc` → `GCC run`. Native-pass tree fixes go back into
`irix7/irix6`; MIPSpro-only constructs are translated in the tooling, never
by editing the tree. The board helper is `scripts/board.py`
(`frontier` / `status` / `show` / `claim` / `advance` / `set` / `done` /
`release`).
