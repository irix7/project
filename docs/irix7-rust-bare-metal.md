# Bare-metal Rust on SGI Indy (IRIX 7)

IRIX 7 replaces IRIX entirely on the SGI Indy — **PROM + kernel running bare metal**, no IRIX 6.5 userspace underneath. This is fundamentally different from the external `rust-irix` projects (e.g. `atomchild411/rust-irix`, `danifunker/rust-irixlibstd`) which target IRIX 6.5 *userspace* with LLVM codegen and IRIX syscalls. Those are **not applicable** to our bare-metal approach.

## Target specs

| Target | File | Arch | ABI | CPU |
|--------|------|------|-----|-----|
| n32/MIPS III | `.scratch/rust-toolchain/targets/mips3-sgi-irix7-n32.json` | mips64 | n32 | mips3 |
| o32/MIPS II | `.scratch/rust-toolchain/targets/mips2-sgi-irix7-o32.json` | mips | o32 | mips2 |

Both use `rust-lld` as linker, `panic = "abort"`, `singlethread = true` (o32), big-endian.

## Rust source

- `rust-src.tar.xz` (5.9 MB) — Rust nightly source (1.101.0-nightly, 2026-10-02)
- `.scratch/rust-toolchain/rust-src-nightly/` — extracted source tree with `builder-config`
- `.scratch/rust-toolchain/no_core.rs` — lang_items skeleton (`Sized`, `Copy`, `PointeeSized`, `MetaSized`, `load`, `store`)
- `.scratch/rust-toolchain/kernel-test/` — Cargo project (`panic = "abort"`, edition 2021)
- `.scratch/rust-toolchain/n32.s` / `o32.s` — compiled assembly output proving rustc can target both ABIs

## Codegen approach

Per ADR-0019, the Rust backend uses **rustc_codegen_gcc** — Rust compiles via GCC codegen rather than LLVM. This requires:

1. **GCC 17 fork with libgccjit** (`irix7/gcc` at `59140d847`) — built via `scripts/build-toolchain.sh --gcc fork --binutils 2.47`
2. **binutils 2.47 IRIX series** (`irix7/binutils-gdb` at `bacb6cc4`) — same build command
3. **Cross toolchain**: `.scratch/toolchain-17.0.0/prefix/bin/mips-sgi-irix6.5-{gcc,ld}` — GCC 17.0.0 + binutils 2.47.50

The GCC fork serves both:
- Stock C/C++ rebuild (IRIX 6.5.7m source → GCC cross build)
- Rust backend codegen (libgccjit for rustc_codegen_gcc)

## What's still needed for bare-metal Rust

| Item | Status |
|------|--------|
| Cross toolchain (GCC 17 + binutils 2.47) | ✅ Built at `.scratch/toolchain-17.0.0/prefix/` |
| Target specs (n32 + o32) | ✅ Exist |
| lang_items / no_core | ✅ Started (`no_core.rs`) |
| Built rustc binary | ❌ Not found — need to build rustc with our target specs |
| gcc-jit installed | ❌ Configured, prefix empty — `build-toolchain.sh --gcc fork` builds GCC but libgccjit install pending |
| Linker script for bare metal | ❌ Need to create |
| PROM code in Rust | ❌ Need to write |
| Kernel in Rust | ❌ Need to write |
| rustc_codegen_gcc backend | ❌ Not yet built |

## External rust-irix projects — NOT applicable

| Project | Target | Why not applicable |
|---------|--------|-------------------|
| `atomchild411/rust-irix` | IRIX 6.5 userspace (LLVM + clang) | We run bare metal, no IRIX underneath |
| `danifunker/rust-irixlibstd` | IRIX 6.5 userspace (o32+n32) | We run bare metal, no IRIX syscalls |

These projects use IRIX syscalls, IRIX libc, and IRIX linker conventions. Our kernel talks directly to hardware (PROM, registers, memory-mapped I/O). Do not fork or reference them for the bare-metal path.

## Build commands

```sh
# Cross toolchain (GCC 17 + binutils 2.47) — already built
nix develop .#rig -c bash -c 'scripts/build-toolchain.sh --gcc fork --binutils 2.47 --sysroot /mnt/europa/sgi-toolchain-scratch/rig/oracle/sysroot'

# Build rustc with our target specs (TBD — need bootstrap process)
# Target specs:
#   --target mips3-sgi-irix7-n32  (kernel, n32/MIPS III)
#   --target mips2-sgi-irix7-o32  (fallback, o32/MIPS II)