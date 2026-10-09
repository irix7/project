# IRIX Toolchain Project

This repository is the hub of the IRIX 6.5 rebuild project.

The project rebuilds IRIX 6.5.7m, then replaces it. The first deliverable is the stock rebuild: IRIX 6.5.7m reproduced in the guest with its own tools. The second is the modernised tree: the same OS rebuilt outside the guest with a modern GCC. The third is IRIX 7: a GPL-3.0 Rust reimplementation of the whole system.

## What is in this repository

| Item | Role |
| --- | --- |
| `patches/gcc-16.2/` | The IRIX patch series for GCC 16.2.0 |
| `patches/binutils-2.47/` | The IRIX patch series for binutils 2.47 |
| `scripts/build-toolchain.sh` | Builds the cross compiler |
| `scripts/verify-toolchain.sh` | Checks the built cross compiler |
| `scripts/smoke.sh` | The smoke harness |
| `scripts/rig/` | The guest install and control scripts |
| `scripts/runtime/` | The libc and libm rebuild scripts |
| `scripts/irix-media/` | Read-only EFS readers for the IRIX CD images |
| `oracle/` | The reference test programs |
| `docs/` | The project documents |
| `docs/adr/` | The project decisions |
| `CONTEXT.md` | The project vocabulary |

## Build the cross compiler

You need two things:

- GNU nix
- A captured IRIX 6.5.7m sysroot (see `docs/oracle.md`)

Run:

```sh
nix develop --command bash -c \
  'scripts/build-toolchain.sh \
    --sysroot /path/to/sysroot --languages c'
scripts/verify-toolchain.sh --prefix .scratch/toolchain-16.2.0/prefix \
  --gcc-version 16.2.0
```

The script verifies the checksum of each pinned tarball. It applies the patch series. It builds into `.scratch/toolchain-16.2.0/prefix`.

The cross targets `mips-sgi-irix6.5`: big-endian MIPS, ELF32, with the o32 and n32 ABIs.

## Test on the guest

The smoke harness compiles a C program with the cross. It copies the binary into the emulated guest. It runs the binary and compares the output with an expected file:

```sh
scripts/smoke.sh --prefix .scratch/toolchain-16.2.0/prefix \
  --cflags "-lm" oracle/hello.c scripts/smoke/hello.expected
```

## Read the documents

| Document | Topic |
| --- | --- |
| `docs/toolchain.md` | The patch series and the build |
| `docs/rig.md` | The emulated guest |
| `docs/oracle.md` | The MIPSpro reference compiler and the sysroot |
| `docs/smoke.md` | The smoke harness |
| `docs/runtime.md` | The libc and libm rebuild |
| `docs/binutils.md` | The binutils series and its evidence |
| `docs/publication.md` | The publication boundary |
| `docs/stack.md` | The full stack: host, cross GCC, Rust, IRIX 7 |
| `docs/adr/` | The project decisions, numbered and dated |

## Related repositories

| Repository | Role |
| --- | --- |
| `irix7/gcc` | The IRIX GCC fork: `rust-lang/gcc` base (GCC 17 trunk + libgccjit) |
| `irix7/binutils-gdb` | The binutils mirror and the IRIX dynamic-link patch |
| `irix7/ghidra` | The Ghidra fork with IRIX support |
| `irix7/irix6` | The IRIX 6.5.7m source tree (private) |
| `irix7/iris` | The Indy emulator fork (fork of `techomancer/iris`) |
| `irix7/harness` | The agent harness (planned) |
| `irix7/os` | IRIX 7, the Rust-native OS (planned) |

## Publication policy

This repository publishes toolchain work only: the patch series, the scripts and the documents. It publishes no IRIX source, headers, binaries or sysroot content. See ADR-0001 and `docs/publication.md`.

## Licence

Each patch keeps the licence of the upstream component. The GCC and binutils patches are GPL. No SGI material is published here.
