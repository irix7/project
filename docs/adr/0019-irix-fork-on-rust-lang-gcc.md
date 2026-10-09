# The IRIX GCC fork is based on rust-lang/gcc (GCC 17 trunk)

The IRIX GCC fork is now based on the `rust-lang/gcc` fork — the GCC 17.0.0 development trunk carrying the libgccjit changes `rustc_codegen_gcc` requires — rather than on upstream GCC 16.2.0. The IRIX patch series is re-derived against that trunk, and the single fork serves both the stock rebuild and the Rust backend's codegen. This trades the stable 16.2 release for a moving development trunk in exchange for one codebase that carries both IRIX support and the patched libgccjit.

**Considered Options**: two tracks — keep 16.2 for the stock rebuild and a separate 17-trunk fork for the backend (rejected — the IRIX patches would live in two codebases); backport the libgccjit changes onto 16.2 (rejected — real backport work against an ABI that `rustc_codegen_gcc` expects from the fork); one fork on the 17 trunk (chosen).

**Consequences**: supersedes ADR-0002's "GCC 16.2 primary" version choice. The `patches/gcc-16.2/` series is re-derived against the `rust-lang/gcc` trunk (pinned to a specific commit), and the toolchain, rig, sysroot and stock-rebuild acceptance are re-validated on it. The Rust backend gains the patched libgccjit from the same codebase.
