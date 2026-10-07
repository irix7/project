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
agent reads the board as ground truth, so never leave it stale. Operate with
`gh project item-edit` / `item-create` / `item-list`; to edit a field by node
id use `gh project item-edit --id <item> --field-id <field> --project-id
<project> --single-select-option-id <option>`.
