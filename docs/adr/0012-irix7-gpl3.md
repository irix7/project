# IRIX 7 is licensed GPL-3.0

The `irix7/os` repository and everything it publishes is licensed GPL-3.0, matching the project's existing GPL toolchain lineage (the GCC/binutils patches are GPL by inheritance, ADR-0001). MIT/Apache-2.0 code remains incorporable into a GPL-3.0 codebase, and the MIT/Apache OS-design references (Theseus, Redox) are used for their patterns, not their code, so they impose no licence obligation. GPL-3.0's anti-tivoisation clause is accepted deliberately.

**Considered Options**: MIT (rejected — permissive but weaker guarantee that derivatives stay open); MIT OR Apache-2.0 dual (rejected — the Rust ecosystem norm, but not the project's lineage); GPL-3.0 (chosen — copyleft, consistent with the toolchain, and protects the "reimplementation of a proprietary UNIX" from being re-closed).
