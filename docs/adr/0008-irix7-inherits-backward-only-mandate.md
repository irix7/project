# IRIX 7 inherits the 6.5 compatibility mandate, without forward compatibility

The end-goal Rust operating system — IRIX 7 — takes IRIX 6.5's application compatibility mandate (007-4405-001) as its own contract, but drops the forward-compatibility half entirely. What survives is the mandate's backward compatibility: a legacy ABI layer runs existing conforming IRIX 6.5 binaries unchanged, and new native Rust interfaces are held to the same no-regressions, backward-only discipline. IRIX 7 promises a binary built on release N runs on every later release; it makes no promise that a binary built on a later release runs on an earlier one, and native IRIX 7 binaries are never expected to run on IRIX 6.5, 5.3, or any earlier release.

**The inherited contract** (the mandate's "Factors That Govern Application Compatibility"):

- ELF executables only — COFF has been dead since 6.2.
- The o32 and n32 ABIs on the Indy, whose R4000-class CPUs are MIPS III. The mandate's own hardware-limitation clause already excludes 64-bit there, matching ADR-0003's n64 scope note.
- The SGI libc and syscall ABI, symbol- and byte-for-symbol, so a conforming 6.5 binary links and runs unchanged.
- Backward compatibility up the release stream, and the no-regressions reliability policy.

**Considered Options**:

- Full up-and-down compatibility (rejected — it is the expensive half of the mandate: the controlled-interface discipline and the `_MIPS_SYMBOL_PRESENT` degrade-gracefully machinery exist only to make forward compatibility work, and carrying them would commit IRIX 7 to teaching every old release to understand every future interface).
- Compatibility philosophy only, with no legacy binaries (rejected — it abandons the entire rebuild investment).
- A legacy ABI layer plus native discipline, backward-only (chosen).

**Consequences**:

- The legacy ABI layer must still honour `_MIPS_SYMBOL_PRESENT` semantics for existing binaries that use it, even though IRIX 7 itself no longer promises forward compatibility.
- New interfaces can be added freely; the legacy layer only ever grows, and never needs to be gateable by release.
- The mandate's kernel caveat (backward compatibility only, since forward cannot be verified) is generalised to the whole OS.
