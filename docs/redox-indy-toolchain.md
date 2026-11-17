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

## Additions, 2026-11-17 (relibc, std and the first userspace)

### Building Rust `std` for the target

- Copy `$(rustc --print sysroot)/lib/rustlib/src/rust/library` (32 MiB) and
  add `libc = { path = ... }` under `[patch.crates-io]` in its `Cargo.toml`;
  select the copy with `__CARGO_TESTS_ONLY_SRC_ROOT`. The libc crate needs
  Redox `mcontext`/`ucontext` definitions for `mips64`.
- Build with `-Zbuild-std=std,panic_abort`. Link with `rust-lld` directly:
  `crt0.o`, `crti.o`, `-L<sysroot>/lib`, `-lc`. `std` on Redox also links
  `-lgcc_s`; with no unwinder, a stub archive must define
  `_Unwind_Backtrace`, `_Unwind_GetIP`, `_Unwind_GetIPInfo`,
  `_Unwind_GetCFA` and `_Unwind_FindEnclosingFunction`.
- cargo does not track a static library passed as a link argument. After
  rebuilding `libc.a`, programs are not relinked unless something else
  changes; put the archive's hash in the rustflags (`--cfg name="hash"`).
- Size: a static `std` program is about 450 KiB with `opt-level=z`, fat LTO,
  one codegen unit and stripped symbols (800 KiB to 1.6 MiB without).

### relibc

- `global_asm!(include_str!("x.S"))` has no C preprocessor: musl's n32
  `setjmp.S`/`longjmp.S` carry `#ifndef __mips_soft_float` blocks that must
  be removed for a soft-float build.
- `objcopy --strip-symbol` refuses the core math symbols on MIPS (`named in
  a relocation`); weaken them instead.
- relibc's Makefile looks for rlibs in `release/deps`; current cargo puts
  them under `release/build/<crate>/<hash>/out`.
- relibc at a given commit locks a kernel commit for the `redox_syscall`
  crate. Build the kernel from that commit: a newer kernel drops system
  calls the libc still issues.

### Byte order and alignment (big-endian n32)

- A struct sent as `[u64]` words whose first member is a `u8` is read by its
  consumers as `word0 as u8`. On big-endian the byte must be last in the
  word. Found in `redox_syscall::StdFsCallMeta`.
- Encoders using `to_ne_bytes` and decoders using `from_le_bytes` only agree
  on little-endian machines.
- MIPS traps on unaligned 64-bit loads (`ld` on an address that is 4 but not
  8 aligned gives an address error). A libc that returns a pointer into a
  packed byte buffer as `struct dirent *` works on x86 and faults here.

### SGI PROM netboot

- The PROM loads ECOFF only into its free-memory area. At `0x8804_0000` a
  6,865,104-byte image loads and a 7,684,304-byte image fails with `Text
  start 0x8040000, size 0x754000 doesn't fit in a FreeMemory area` (iris,
  embedded IP24 PROM, 32 MiB).

### PROM autoboot over the network (checked on iris, 2026-11-17)

- `setenv SystemPartition bootp()`, `setenv OSLoader redox`,
  `setenv AutoLoad Yes`: power-on, and every jump to the PROM reset vector,
  netboots the file `redox` with nothing typed.
- The PROM will not autoload when its console cannot open and records
  `NoAutoLoad=CONSOLE OPEN FAILED.` in the environment. Headless iris needs
  `setenv console d` as well.
- `init` at the Command Monitor returns to the maintenance menu; it does not
  autoload. A reset does.
- New threads on n32: the stack pointer must be 16-byte aligned at the first
  call; relibc pushes seven 4-byte words for the thread start, so the
  trampoline realigns.

## First boot on the real Indy (2026-11-17)

- The lab dnsmasq has `dhcp-boot=irix7`. The PROM fetches the file named in
  the BOOTP reply, so `boot -f bootp()redox` loads `irix7`. `make indy-netboot
  INDY_TFTP_NAMES="redox irix7"` installs the Redox image under both names;
  the sibling image is kept as `/var/lib/sgi-netboot/irix7.os-backup`.
- The same dnsmasq has `dhcp-ignore=tag:!bootp`, so a DHCP client gets no
  lease. The image falls back to 10.1.1.112/24 after 20 seconds.
- iris runs a transparent cache model by default (`tcache`, part of `jitv2`).
  Cache bugs do not show there. Run the gate with `IRIS_ARGS="--cache full"`
  (and `--cpu r5000`) before trying real hardware.
- With the full cache model the kernel stopped at its first kseg2 access with
  exception 31 (VCED). An R4000/R4400 with a secondary cache raises VCED/VCEI
  when a page is touched under a second virtual colour. The handler
  invalidates the primary line (`Index_Store_Tag_D` with TagLo 0), runs
  `Hit_Writeback_Inv_SD` on BadVAddr and returns.
- The PROM console is usable from the kernel: system parameter block at
  0xA0001000 (signature 0x53435241), firmware vector pointer at +0x20, `Write`
  is entry 27, `GetMemoryDescriptor` 18, `GetEnvironmentVariable` 30. The PROM
  is 32-bit o32: give it 16 bytes of argument space and save s0..s7, fp and gp
  as 64 bits around the call. `ConsoleOut` is `serial(0)` or `video()`.
- PROM memory descriptors on a 32 MiB Indy: free 0x08002000..0x08740000,
  firmware temporary 0x08740000..0x08800000, free 0x08800000..0x0A000000.
  Leave the firmware range alone while its console is in use.
