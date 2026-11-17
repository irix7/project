# Toolchain notes from the Redox-on-Indy port

Findings from `irix7/redox` (Redox OS on the SGI Indy, MIPS III n32 big-endian,
32 MiB), recorded here because they concern the toolchains this repository
owns. Dated 2026-11-17; each entry names what proved it.

## Rust

| Item | Value |
|---|---|
| Channel | `nightly-2026-10-03` with `rust-src`, `llvm-tools-preview` |
| Target JSON | `irix7/redox` `targets/mips64-sgi-redox.json` (userspace: `os = redox`, `env = relibc`) and the kernel's `targets/mips64-unknown-kernel.json` (`os = none`) |
| Fields that matter | `arch mips64`, `llvm-abiname n32`, `cpu mips3`, `features +mips3,+soft-float,+noabicalls`, `data-layout E-m:e-p:32:32-...-n32:64-S128`, `target-pointer-width 32`, `max-atomic-width 64`, `relocation-model static`, `linker rust-lld`, `linker-flavor gnu-lld` |
| build-std | `-Zbuild-std=core,alloc -Zbuild-std-features=compiler-builtins-mem -Zjson-target-spec`; `.json` targets need `-Zjson-target-spec` on every cargo call |
| Inline asm | `#![feature(asm_experimental_arch)]` in every crate that uses `asm!`/`global_asm!` on `mips64` |

### rust-lld and the n32 object format

- `OUTPUT_FORMAT("elf32-ntradbigmips")`. With `elf32-tradbigmips` lld refuses
  the objects (`is incompatible with elf32-tradbigmips`): n32 objects are
  ELF32 with `EF_MIPS_ABI2`.
- `la` of a symbol in a mips64 asm block warns `la used to load 64-bit
  address`. It expands to lui/addiu and sign-extends, which is what a 32-bit
  kernel in kseg0 wants; the warning is noise.
- An `in(reg)` operand may be allocated `$1` (`$at`). LLVM then warns when a
  macro instruction in the same block needs `$at`. Use `.set noat` or avoid
  macro instructions (`la`, `li` with 32-bit immediates) next to operands.
- `$31` cannot be named as a clobber (`the return address register cannot be
  used as an operand`); save it on the stack around a `bal`.
- `addiu` takes a 16-bit immediate: `addiu $sp, $sp, 65536` does not
  assemble; load the constant with `li` and `addu`.
- In `.set noreorder` blocks every branch and jump needs its delay slot
  filled by hand; in the default mode the assembler inserts the nop.

### Register width

n32 keeps 32-bit values sign-extended in 64-bit registers and compares them
as 64-bit values. A kernel that writes a user register (syscall result, PC,
SP, arguments) must sign-extend: a zero-extended `usize::MAX - errno` made
`Error::demux` miss every error and the Redox bootstrap spun on retries.
Store with `(value as u32 as i32 as i64) as u64`.

### Thread-local storage

LLVM lowers every thread-local access on MIPS through `rdhwr $rt, $29`
(the user-local register), which the R4400 does not have. The Redox kernel
emulates it in the reserved-instruction handler: `insn & 0xffe0_ffff ==
0x7c00_e83b`, write the thread pointer into `$rt`, step EPC by 4. The thread
pointer is kept in `$gp` (`$28`), which static `+noabicalls` code never uses.
Static TLS follows the MIPS variant I layout: `tp = tls_start + 0x7000`, and
the linker applies the `-0x7000` bias to `%tprel` relocations.

## C: the Bootlin MIPS n32 toolchain

| Item | Value |
|---|---|
| Toolchain | `mips64-n32--uclibc--stable-2026.08-1`, GCC 15.3.0, binutils 2.45.1, uClibc-ng 1.0.59 |
| Durable copy | `/mnt/europa/toolchains/mips64-n32--uclibc--stable-2026.08-1` (380 MB) |
| After moving | run `relocate-sdk.sh` in the SDK root; the `gcc` wrapper then finds `cc1` |
| Freestanding flags | `-mabi=n32 -EB -march=mips3 -msoft-float -mno-abicalls -fno-pic -G0 -ffreestanding -nostdlib` |
| Checked | an object built with those flags reads as ELF32 big-endian, `EF_MIPS_ABI2`, `EF_MIPS_ARCH_3` |

The copy under `/tmp/opencode/relibc-n32/toolchain` is incomplete: the
`bin/` wrappers and `libexec/gcc/.../cc1` are missing and
`gcc.br_real` fails with `cannot execute 'cc1'`. Do not point builds at it.

relibc needs a C compiler only for `src/c/stdlib.c` (long double helpers) and
openlibm. With `USE_RUST_LIBM=1` openlibm is replaced by the Rust `libm`
crate, so the C requirement shrinks to one file. The `cc` crate reads
`CC_mips64_sgi_redox` and `CFLAGS_mips64_sgi_redox`.

## Emulator

`iris` (`irix7/iris`) is the gate: headless, the embedded IP24 PROM puts its
menu on the serial console, `iris-ci serial-send` types at it and
`boot -f bootp()redox` loads an ECOFF `OMAGIC` image from `tftp_dir`. The
ELF-to-ECOFF converter is `irix7/redox` `tools/ecoff`.
