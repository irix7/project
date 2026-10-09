# Context Map

Two contexts share this vocabulary. The toolchain context lives here; the IRIX 7
operating-system context lives in the separate `irix7/os` repo (planned) and will
carry its own `CONTEXT.md`. This map fixes the boundary so a term is defined once
and cross-referenced, never duplicated. (The usual Context Map points at in-repo
`src/<context>/` directories; here the second context is a sibling repo, so the
pointer is a repo name rather than a path.)

## Contexts

- [SGI IRIX Toolchain](./CONTEXT.md) — this repo. Rebuilding IRIX 6.5.7m with its
  own tools, then with GCC: the rig and board that drive it, and the clean-room
  machinery that turns the private source into a behavioural specification.
- IRIX 7 OS (`irix7/os`, planned) — the operating system itself: kernel, native
  and legacy ABIs, and the identity services. It will own those terms in its own
  `CONTEXT.md`.

## Vocabulary boundary

**Defined here (toolchain context)**

- Rebuild stages: `Stock rebuild`, `Modernised tree`, `Target`, `Baseline`
- Toolchain and rig: `Patch series`, `Sysroot`, `Oracle`, `Guest`, `Rig`, `Smoke harness`
- Board: `Board item`, `Subsystem`, `Product`, `Lane`, `Frontier`
- Clean room: `Clean room`, `Transcription`, `Behavioural specification`, `Semantic IR`, `Dirty readers`, `Clean writers`, `Structural split`, `Similarity screen`

**Defined in IRIX 7 (moves to `irix7/os/CONTEXT.md`)**

- `IRIX 7`, `Legacy ABI layer`, `Native ABI`, `Compat container`, `Modern equivalent`
- Identity services named but not yet defined: `sproc`, XFS, real-time scheduling,
  graphics and digital media, hardware inventory, topology. These are OS concepts
  and are deliberately left undefined here.

**Shared, owned here until the split**

- `Conforming IRIX 6.5 binary` — the legacy ABI layer runs it, and the toolchain's
  sysroot and oracle are built to satisfy it; defined here, cross-referenced by IRIX 7.
- `Clean room` and its machinery — the contract for what `irix7/os` may publish.

## Relationships

- **Toolchain → IRIX 7**: the `Stock rebuild` and `Modernised tree` reproduce IRIX
  6.5.7m; the clean-room `Transcription` produces the `Behavioural specification`
  that the IRIX 7 context reimplements in Rust.
- **Shared contract**: `Conforming IRIX 6.5 binary` and the `Clean room` are the
  interface between the two contexts.

## Migration

When `irix7/os` is created, move the **Defined in IRIX 7** entries to its
`CONTEXT.md`, leave the toolchain and clean-room sets here, and replace the moved
entries with cross-references. Until then they stay here so this repo's ADRs
resolve.
