# Bare-metal Rust on SGI Indy (IRIX 7)

IRIX 7 replaces IRIX entirely on the SGI Indy — **PROM + kernel running bare metal**, no IRIX 6.5 userspace underneath. This is fundamentally different from the external `rust-irix` projects (e.g. `atomchild411/rust-irix`, `danifunker/rust-irixlibstd`) which target IRIX 6.5 *userspace* with LLVM codegen and IRIX syscalls. Those are **not applicable** to our bare-metal approach.

## Key finding: we do NOT need to build rustc from source

A host nightly rustc is already installed and matches the version that produced the existing `n32.s`/`o32.s` proof-of-concept assembly:

```
rustc 1.101.0-nightly (db8f076d2 2026-10-03)
host: x86_64-unknown-linux-gnu
LLVM version: 23.1.1
```

Location: `/home/matt/.rustup/toolchains/nightly-x86_64-unknown-linux-gnu/bin/rustc`
Cargo: `/home/matt/.rustup/toolchains/nightly-x86_64-unknown-linux-gnu/bin/cargo`

The `rust-src.tar.xz` extraction under `.scratch/rust-toolchain/rust-src-nightly/rust-src/` is **incomplete** — it contains only the standard-library sources (`lib/rustlib/src/rust/library/`) plus `llvm-project` under `lib/rustlib/src/rust/src/`. There is **no** `src/compiler`, `src/bootstrap`, or build system, so rustc **cannot** be bootstrapped from this extraction. Since a working host nightly is present, bootstrapping from source is unnecessary and not possible with what is extracted.

## Environment

| Item | Path / value |
|------|--------------|
| Host rustc | `/home/matt/.rustup/toolchains/nightly-x86_64-unknown-linux-gnu/bin/rustc` |
| Host cargo | `/home/matt/.rustup/toolchains/nightly-x86_64-unknown-linux-gnu/bin/cargo` |
| rust-lld | `/home/matt/.rustup/toolchains/nightly-x86_64-unknown-linux-gnu/lib/rustlib/x86_64-unknown-linux-gnu/bin/rust-lld` |
| Cross gcc | `.scratch/toolchain-17.0.0/prefix/bin/mips-sgi-irix6.5-gcc` (GCC 17.0.0) |
| Cross ld | `.scratch/toolchain-17.0.0/prefix/bin/mips-sgi-irix6.5-ld` (binutils 2.47) |
| Cross as | `.scratch/toolchain-17.0.0/prefix/bin/mips-sgi-irix6.5-as` |
| Target specs | `.scratch/rust-toolchain/targets/mips3-sgi-irix7-n32.json`, `mips2-sgi-irix7-o32.json` |
| Linker script | `.scratch/rust-toolchain/linker.ld` |
| Entry stub | `.scratch/rust-toolchain/start.s` |
| lang_items skeleton | `.scratch/rust-toolchain/no_core.rs` |
| Kernel test crate | `.scratch/rust-toolchain/kernel-test/` |
| gcc-jit | `.scratch/rust-toolchain/gcc-jit/` (prefix EMPTY — not installed) |

## Target specs

| Target | File | Arch | ABI | CPU | Pointer |
|--------|------|------|-----|-----|---------|
| n32/MIPS III | `.scratch/rust-toolchain/targets/mips3-sgi-irix7-n32.json` | mips64 | n32 | mips3 | 32 |
| o32/MIPS II | `.scratch/rust-toolchain/targets/mips2-sgi-irix7-o32.json` | mips | o32 | mips2 | 32 |

Both use `rust-lld` as linker, `gnu-lld` flavour, `panic = "abort"`, big-endian, soft-float, `+noabicalls`.

## How to use a custom target spec with the host rustc

Custom (user-supplied) target specs are unstable on nightly and require `-Zunstable-options`. Pass the JSON path directly to `--target`:

```sh
export RUSTC=/home/matt/.rustup/toolchains/nightly-x86_64-unknown-linux-gnu/bin/rustc
export TARGETS=$HOME/projects/sgi-toolchain-new/.scratch/rust-toolchain/targets

# Compile no_core.rs to assembly for n32 (MIPS III / n32 ABI)
$RUSTC -Zunstable-options \
  --target $TARGETS/mips3-sgi-irix7-n32.json \
  --crate-type lib --emit=asm \
  $HOME/projects/sgi-toolchain-new/.scratch/rust-toolchain/no_core.rs

# Compile no_core.rs to assembly for o32 (MIPS II / o32 ABI)
$RUSTC -Zunstable-options \
  --target $TARGETS/mips2-sgi-irix7-o32.json \
  --crate-type lib --emit=asm \
  $HOME/projects/sgi-toolchain-new/.scratch/rust-toolchain/no_core.rs
```

## Verified codegen output

Both targets compile successfully to ELF32 relocatable objects:

```
# n32 object
ELF 32-bit MSB relocatable, MIPS, N32 MIPS-III version 1 (SYSV)
Flags: 0x20000021, noreorder, abi2, mips3

# o32 object
ELF 32-bit MSB relocatable, MIPS, MIPS-II version 1 (SYSV)
Flags: 0x10001001, noreorder, o32, mips2
```

The existing `n32.s` / `o32.s` files were produced with this exact rustc (`db8f076d2 2026-10-03`) and match fresh output (modulo the `.ident` line).

## Bare-metal linker script

Created at `.scratch/rust-toolchain/linker.ld`. Memory model for the Indy:

- **PROM** (firmware): physical `0x1fc00000` (kseg0 `0xbfc00000`), read-only executable. The CPU reset vector starts here.
- **RAM** (kernel): physical `0x00000000` (kseg0 `0x80000000`). PROM loads the kernel image here and jumps to the entry point.

Linker script contents:

```
ENTRY(kernel_start)

MEMORY
{
  prom  (rx)  : ORIGIN = 0x1fc00000, LENGTH = 0x00400000   /* 4 MiB PROM */
  ram   (rwx) : ORIGIN = 0x80000000, LENGTH = 0x02000000   /* 32 MiB RAM */
}

SECTIONS
{
  . = 0x80000000;
  .text : ALIGN(4) { _stext = .; *(.text*); _etext = .; } > ram
  .rodata : ALIGN(4) { _srodata = .; *(.rodata*); _erodata = .; } > ram
  .data : ALIGN(4) { _sdata = .; *(.data*); _edata = .; } > ram
  .bss : ALIGN(4) { _sbss = .; *(.bss*); *(COMMON); _ebss = .; } > ram
  _end = .;
  .stack (NOLOAD) : ALIGN(8) { _stack_bottom = .; *(.stack*); _stack_top = .; } > ram
  _start = kernel_start;
  /DISCARD/ : { *(.eh_frame*); *(.comment*); *(.note.GNU-stack*); ... }
}
```

## End-to-end link test (WORKS)

A minimal `kernel_start`/`_start` entry stub was written at `.scratch/rust-toolchain/start.s`. It sets `$sp` to `_stack_top` and hangs. Assembling and linking:

```sh
export AS=.scratch/toolchain-17.0.0/prefix/bin/mips-sgi-irix6.5-as
export LD=.scratch/toolchain-17.0.0/prefix/bin/mips-sgi-irix6.5-ld
export LS=.scratch/rust-toolchain/linker.ld

# Assemble the entry stub (use -msoft-float to match rustc's +soft-float)
$AS -msoft-float -o /tmp/start.o .scratch/rust-toolchain/start.s

# Link entry stub + Rust object into a bare-metal kernel ELF
$LD -T $LS -o /tmp/kernel-n32.elf /tmp/start.o /tmp/test-n32.o
```

Result:
- **Type:** ELF32 EXEC (executable), big-endian, MIPS R3000
- **Entry point:** `0x80000030` (kernel_start)
- **LOAD segment:** VMA == LMA == `0x80000000`, flags RWE
- **.text** at `0x80000000`, **.bss** at `0x80000080`, **.stack** at `0x80000080`

This proves the full pipeline: host rustc → target-spec codegen → cross GNU ld → bare-metal kernel ELF at `0x80000000`.

## Linker choice

| Linker | Status | Notes |
|--------|--------|-------|
| Cross GNU ld (`mips-sgi-irix6.5-ld`) | ✅ Works | Native MIPS cross linker; correct placement at `0x80000000`. Use `-C linker=mips-sgi-irix6.5-ld` to override rustc's default. |
| rust-lld | ⚠️ Investigation needed | Direct invocation with `-flavor gnu` reports a float-ABI mismatch (`-msoft-float` object vs `-mdouble-float` default). rustc's own integration may handle flags differently; not yet verified end-to-end. |

## gcc-jit / rustc_codegen_gcc

Per ADR-0019, the Rust backend is planned to use `rustc_codegen_gcc` (GCC codegen via libgccjit) rather than LLVM.

- gcc-jit source is configured at `.scratch/rust-toolchain/gcc-jit/build/` but the **prefix is empty** — `libgccjit` has **not** been installed (`make install` not yet run).
- Not required for the basic LLVM-based rustc path above; only needed when switching to the GCC codegen backend.

## What works / what doesn't

| Item | Status | Detail |
|------|--------|--------|
| Host rustc (nightly 1.101.0) | ✅ Available | `~/.rustup/toolchains/nightly-x86_64-unknown-linux-gnu/bin/rustc` |
| Target spec loading | ✅ Works | Requires `-Zunstable-options --target <path.json>` |
| n32 codegen | ✅ Works | ELF32 MIPS-III N32 |
| o32 codegen | ✅ Works | ELF32 MIPS-II o32 |
| Bare-metal linker script | ✅ Created | `.scratch/rust-toolchain/linker.ld`, entry `0x80000000` |
| End-to-end ELF link | ✅ Works | cross GNU ld + linker.ld, entry `0x80000030` |
| Build rustc from source | ❌ Not possible | Extraction lacks `src/compiler`/`src/bootstrap`; not needed anyway |
| rust-lld direct link | ❌ Fails | Float-ABI mismatch; cross ld is the working path |
| gcc-jit installed | ❌ No | Prefix empty; `make install` pending |
| `_start`/`kernel_start` stub | ✅ Created | `.scratch/rust-toolchain/start.s`, sets SP and hangs |
| PROM code in Rust | ❌ Not written | Needed for real boot |
| Kernel in Rust | ❌ Not written | `no_core.rs` is only a lang_items skeleton |

## Next steps

1. Write a real `kernel_main` in Rust (extern "C", `no_mangle`) and have `start.s` call it instead of hanging.
2. Resolve the `-msoft-float` vs `-mhard-float` mismatch: assemble `start.s` with `-msoft-float` (done above) or make the target spec not use soft-float.
3. Investigate rust-lld as the linker (target spec default) or keep overriding with the cross GNU ld.
4. Wire the linker script into rustc invocations via `-C link-arg=-T<path>/linker.ld`.
5. Document the PROM→kernel boot flow: PROM at `0x1fc00000` loads image to `0x80000000`, jumps to `kernel_start`.
6. Update the rebuild board (`scripts/board.py`) when source/build status changes; do not commit SGI material (ADR-0001).

## External rust-irix projects — NOT applicable

| Project | Target | Why not applicable |
|---------|--------|-------------------|
| `atomchild411/rust-irix` | IRIX 6.5 userspace (LLVM + clang) | We run bare metal, no IRIX underneath |
| `danifunker/rust-irixlibstd` | IRIX 6.5 userspace (o32+n32) | We run bare metal, no IRIX syscalls |

These projects use IRIX syscalls, IRIX libc, and IRIX linker conventions. Our kernel talks directly to hardware (PROM, registers, memory-mapped I/O). Do not fork or reference them for the bare-metal path.
