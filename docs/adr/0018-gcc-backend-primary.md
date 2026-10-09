# Rust toolchain for IRIX 7: GCC codegen backend primary, LLVM in parallel

IRIX 7's Rust toolchain invests in `rustc_codegen_gcc`, using the project's custom IRIX cross-GCC (libgccjit) as rustc's codegen backend, because GCC's MIPS backend covers MIPS III + n32 cleanly where LLVM cannot do MIPS III + o32. The backend is immature (no verified MIPS support, unsupported cross-targeting), so LLVM custom targets are kept as a parallel path so early work — the o32/MIPS II and n32/MIPS III targets and the tracer bullet — is not blocked. The GCC backend becomes primary once its MIPS support is proven.

**Consequences**: the toolchain effort gains a track: build libgccjit into the IRIX cross-GCC, wire `rustc_codegen_gcc`, and close its MIPS ABI gaps (rust-lang/rustc_codegen_gcc #546, #366). ADR-0010's n32 kernel decision stands; its "LLVM custom target" consequence is superseded here. The o32 ECOFF bootstrap (ADR-0016) is Rust compiled o32/MIPS II, matching the everything-in-Rust rule.
