# Reference-first rebuild in the guest

Before the new toolchain is substituted into the tree, the tree's native build is reproduced inside the guest with SGI's own tools (MIPSpro, smake, lboot) and used as ground truth for headers, object lists, flags and link lines. Every later discrepancy is then attributable to the toolchain rather than to a reconstructed build system.

**Considered Options**: GCC-first on the tree (rejected — it mixes two classes of unknown failure); deferring the choice until M1/M2 (rejected — the native build is nearly free once MIPSpro is installed for the oracle, and it de-risks the rebuild's critical path early).
