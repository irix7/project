# The IRIX 7 stack

The full vertical, from build host down to the operating system. Each layer
names the technology, the decision that fixed it (ADR), and its current status.

## 1. Build host

| Component | Choice | Note |
| --- | --- | --- |
| Environment | GNU Nix (`flake.nix`) | pinned nixpkgs; `#default` and `#rig` devshells |
| Host compiler | GCC 13 | matches the pdaxrom-proven host |

## 2. Cross toolchain — target `mips-sgi-irix6.5`

| Component | Choice | Note |
| --- | --- | --- |
| binutils | 2.47, IRIX series | `patches/binutils-2.47/` |
| GCC | 17.0.0 trunk, `rust-lang/gcc` fork | IRIX series (`patches/gcc-16.2/`, 8 patches) applies clean to 17 trunk; ADR-0019 |
| jit | `libgccjit` built into the fork | `--enable-host-shared --enable-languages=c,jit`; the Rust codegen backend |
| ABI | o32 (default), n32, n64 | ELF32 big-endian MIPS III/IV; ADR-0003 |
| sysroot | captured IRIX 6.5.7m headers + libs | eventually produced by the rebuild itself |

## 3. Rust toolchain

| Component | Choice | Note |
| --- | --- | --- |
| rustc | nightly | `-Z build-std` for `core`/`alloc` |
| codegen backend | `rustc_codegen_gcc` (libgccjit from §2) | primary; ADR-0018 |
| parallel path | LLVM custom targets | `mips2-sgi-irix7-o32` (MIPS II), `mips3-sgi-irix7-n32` (MIPS III); codegen proven |
| LLVM gap | "MIPS III + o32" not buildable | so o32 implies MIPS II |

## 4. IRIX 7 — the operating system

| Component | Choice | Note |
| --- | --- | --- |
| bootstrap | o32 ECOFF, Rust, MIPS II | ARCS is o32/ECOFF-only; ADR-0016 |
| kernel | monolithic Rust, n32/MIPS III | deep crates, Theseus-style language isolation; ADR-0010 |
| native ABI | capability/object model | handles + rights, jobs, channels, VMO/VMAR, ports; ADR-0011 |
| legacy ABI layer | compat container over the object model | ELF o32/n32, SGI libc + syscall ABI; ADR-0011 |
| userland | Rust reimplementation of everything | modern open-source equivalents are references only; ADR-0013, ADR-0017 |
| identity | `sproc`, XFS, real-time, graphics/media, hinv, topology | STREAMS legacy-compat only; ADR-0013 |
| licence | GPL-3.0 | ADR-0012 |

## 5. Tooling & harness

| Component | Choice | Note |
| --- | --- | --- |
| harness | `irix7/harness` | clean-room: structural split + verification; ADR-0014 |
| spec | layered semantic IR + per-subsystem docs | ADR-0015 |
| rig / oracle | emulator + MIPSpro + sysroot | ground truth for ABI conformance |

## Current status

- Custom Rust targets codegen for o32 and n32 — **proven**.
- IRIX patch series applies clean to `rust-lang/gcc` (17 trunk) — **proven**.
- `-Z build-std` for the targets — blocked on host-toolchain glue (nix rustup self-contained linker).
- GCC-backend build — blocked on disk space (99 % full).
