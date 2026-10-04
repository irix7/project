# SGI IRIX Toolchain

A toolchain and rebuild project for IRIX 6.5.7m on the SGI Indy: reproduce the operating system privately with a modern GCC, then modernise it.

## Language

**Stock rebuild**:
Reproducing IRIX 6.5.7m's own components (userland, libraries, firmware, kernel) from its source tree with the new toolchain, without changing operating-system behaviour. The project's first deliverable.

**Modernised fork**:
The later deliverable that changes the rebuilt code (driver replacements, modernisation work) to make a new IRIX-derived OS version. Depends on the stock rebuild succeeding. IRIX Community Edition and its NewEOE utilities are the community's prior art for this phase and a vocabulary reference, not a dependency of the stock rebuild.

**Target**:
The toolchain's output platform: `mips-sgi-irix6.5`, ELF32 big-endian MIPS III/IV, with o32, n32 and n64 ABIs.

**Baseline**:
A known-good community toolchain reproduced before the GCC 16 forward-port, used to validate the rig and sysroot loop and to seed the IRIX OS layer.
_Avoid_: reference toolchain, starting point

**Patch series**:
The IRIX OS layer carried as patches against tagged GCC and binutils releases; the public fork is its home and the series, not a long-lived branch, is the source of truth.
_Avoid_: fork branch

**Sysroot**:
The IRIX headers, startfiles and libraries a link needs. Captured from the guest first; eventually produced by the stock rebuild itself.

**Native reference build**:
The tree's own build reproduced inside the guest with SGI's original tools, used as ground truth for headers, object lists, flags and link lines before the new toolchain replaces them.
_Avoid_: golden build

**Oracle**:
MIPSpro or IDO, or a recompiled form of them running on a modern host, used to arbitrate what correct IRIX output looks like when the new toolchain disagrees.

**Guest**:
The emulated IRIX system on which test binaries are executed: a fresh IRIX 6.5.7m install matching the rebuild target.

**Rig**:
The emulator, disks, console and control interface that run the guest and move test binaries in and out.
_Avoid_: emulator (when the whole setup is meant)
