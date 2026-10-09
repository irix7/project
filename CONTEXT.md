# SGI IRIX Toolchain

A toolchain and rebuild project for IRIX 6.5.7m on the SGI Indy: rebuild it privately with IRIX's own tools, then with a modern GCC, then reimplement it in Rust as IRIX 7.

## Language

**Stock rebuild**:
Reproducing IRIX 6.5.7m's own components (userland, libraries, firmware, kernel) inside the guest with IRIX's own tools (MIPSpro `cc`, `smake`), changing no operating-system behaviour. The project's first deliverable and the ground truth for headers, object lists, flags and link lines (ADR-0005).
_Avoid_: native reference build, golden build, starting point

**Modernised tree**:
The stage after the stock rebuild: IRIX 6.5.7m rebuilt with the new GCC outside the guest, its source modernised only enough to satisfy the compiler and change no operating-system behaviour. The bridge from the stock rebuild to IRIX 7. IRIX Community Edition and its NewEOE utilities are the community's prior art for this phase and a vocabulary reference, not a dependency.
_Avoid_: modernised fork, intermediate

**Target**:
The toolchain's output platform: `mips-sgi-irix6.5`, ELF32 big-endian MIPS III/IV, with o32, n32 and n64 ABIs.

**Baseline**:
A known-good community toolchain reproduced before the GCC 16 forward-port, used to validate the rig and sysroot loop and to seed the IRIX OS layer.
_Avoid_: reference toolchain, starting point

**Patch series**:
The IRIX OS layer carried as patches against the `rust-lang/gcc` fork (GCC 17 trunk) and tagged binutils releases; the series is the source of truth, committed to `master` of `irix7/gcc`.
_Avoid_: fork branch

**Sysroot**:
The IRIX headers, startfiles and libraries a link needs. Captured from the guest first; eventually produced by the stock rebuild itself.

**Header farm**:
The headers the new toolchain compiles against, collected from the tree and the install media alongside the captured sysroot. A header-only board item exists to make sure every header is present in the tree.

**Oracle**:
MIPSpro or IDO, or a recompiled form of them running on a modern host, used to arbitrate what correct IRIX output looks like when the new toolchain disagrees.

**Guest**:
The emulated IRIX system on which test binaries are executed: a fresh IRIX 6.5.7m install matching the rebuild target.

**Rig**:
The emulator, disks, console and control interface that run the guest and move test binaries in and out.
_Avoid_: emulator (when the whole setup is meant)

**Smoke harness**:
The script that compiles a test program against the captured sysroot, runs it
on the guest and diffs stdout against expected output; the project's primary
testing seam, dynamic-first per ADR-0006.
_Avoid_: test runner

**Board item**:
One row of the IRIX 6.5.7m rebuild board and the unit of claim: a single buildable command, flattened out of its `.sw.*` product so parallel workers never contend for one row.
_Avoid_: task, ticket

**Subsystem**:
A board item's product family, used as the board's coarse grouped-view field; the exact product name lives in `Product`.

**Product**:
The IRIX `.sw.*` software product a board item's command ships in, carried as a text field because the product set is larger than the board's single-select cap.

**Lane**:
One of the two work queues on the board: recompile (items with `src: full`) or reconstruct (items with `src: stub`/`none`, decompiled to C first). A `src: missing` item joins the reconstruct lane once its binary is found; only an item with neither source nor binary nor media gates nothing.
_Avoid_: stream, track

**Frontier**:
The board's claimable items right now: unclaimed, with a next action available.

**IRIX 7**:
The end-goal operating system: a GPL-3.0 Rust reimplementation of everything on the IRIX discs, running existing conforming IRIX 6.5 binaries through a legacy ABI layer and offering a new native interface under the same backward-only, no-regressions discipline. A monolithic Rust kernel (n32/MIPS III) carrying IRIX's identity — `sproc`, XFS, real-time scheduling, graphics and digital media, hardware inventory, topology awareness — as first-class services, not Linux or BSD (ADR-0008, ADR-0010..0014).

**Legacy ABI layer**:
The IRIX 7 component that runs existing conforming IRIX 6.5 binaries unchanged: ELF, the o32 and n32 ABIs, the MIPS ISA, and the SGI libc and syscall ABI, reimplemented in Rust. Built as a compat container over the kernel's object model, not as a second kernel (ADR-0011).
_Avoid_: compatibility shim

**Conforming IRIX 6.5 binary**:
An existing third-party binary that uses only IRIX 6.5's documented ABI — ELF, o32 or n32, the MIPS ISA, and the SGI libc and syscall interface — and so runs unchanged under the legacy ABI layer. SGI's own products are not carried this way; they are reimplemented in Rust (ADR-0008, ADR-0013).
_Avoid_: legacy binary, IRIX binary

**Native ABI**:
The IRIX 7 interface for new Rust programs: a capability/object model (per-process handle tables with rights, jobs, channels, VMAR/VMO memory objects, ports for async completion), distinct from the C ABI and carrying no ambient authority (ADR-0011).
_Avoid_: syscall layer (when the capability model is meant)

**Compat container**:
How the legacy ABI layer is implemented: a translation layer that maps IRIX 6.5 syscalls onto the same kernel objects the native ABI uses, so files, memory and processes have one source of truth (ADR-0011).

**Clean room**:
The boundary that keeps the public `irix7/os` repo free of SGI-derived expression (ADR-0009). Enforced mechanically by the agent harness through the structural split and the similarity screen (ADR-0014, ADR-0015).

**Transcription**:
The clean-room process in which the dirty readers read the private IRIX source and produce the behavioural specification; its output is derivative of the licence-restricted tree and stays private (ADR-0009). Hybrid: automated parse-to-AST for the mechanical parts, agents for the semantic parts (ADR-0014).
_Avoid_: translation, port

**Behavioural specification**:
What the transcription produces for the clean writers: interfaces, symbol names, structure layouts and documented semantics (ADR-0009). Layered into the semantic IR and per-subsystem documents (ADR-0015). Private.
_Avoid_: clean-room spec, interface dump

**Semantic IR**:
The machine-readable core of the behavioural specification: the source→AST→Rust AST abstracted to behaviour, and the single machine-checkable source of truth (ADR-0015).
_Avoid_: parse tree, AST

**Dirty readers**:
The transcription-side agents that see the private IRIX source and emit the behavioural specification. Held apart from the clean writers by the structural split (ADR-0014).

**Clean writers**:
The reimplementation-side agents that see only the behavioural specification and emit the Rust published to `irix7/os`. Held apart from the dirty readers by the structural split (ADR-0014).

**Structural split**:
The harness guarantee that the dirty readers and clean writers never meet: the clean writers receive only the specification, never the private source (ADR-0014).
_Avoid_: sandbox

**Similarity screen**:
The harness's verification of clean-side output: a token and structure similarity-diff against the private source, with flagged matches sent to a human before anything reaches `irix7/os` (ADR-0015).
_Avoid_: plagiarism checker

**Modern equivalent**:
A modern open-source version of an IRIX component — the same software now open-sourced, or a faithful reimplementation — used as a design reference; the component is still reimplemented in Rust (ADR-0017).
_Avoid_: upstream port, vendor
