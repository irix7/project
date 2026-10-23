# Native Rust `std` is load-bearing; its libc baseline is POSIX.1-2024

IRIX 7's native world needs the real Rust standard library. Without it every
native application — the desktop, the MCP cell, network and filesystem clients,
ordinary user programs — would be `no_std` and would reimplement strings,
vectors, files, threads, sockets and time. The native world is the future of the
product (ADR-0010, ADR-0011), so its standard library is load-bearing rather
than a later convenience.

The native `std` is built for a hosted n32 target with `target_os = "irix7"` and
a Unix `target-family`, against a native libc whose normative **source API** is
POSIX.1-2024 (IEEE Std 1003.1-2024, the core of The Open Group Base
Specifications Issue 8 and Single UNIX Specification, Version 5 (2024)), plus a
documented XSI subset and a documented extension list. POSIX is a source
contract, not a binary ABI: IRIX 7 owns the native n32 symbol names, struct
layouts, `errno` values, TLS model, startup, dynamic loading and
syscall/capability entry. The capability substrate stays underneath the
Unix-shaped libc (ADR-0011).

The IRIX 6.5 compatibility container is a separate target and source contract.
It keeps the IRIX 6.5 o32/n32 ABI, syscall numbers, libc layouts and `sproc`
semantics; POSIX.1-2024 is not retrofitted onto it, and a compatibility `std` is
a legacy-container test track, not the native ABI. The kernel, ROM and loadable
modules remain `no_std` and must not link `std`.

**Considered Options**: (a) an IRIX-6.5-shaped native libc, rejected because it
would make the new OS inherit 1990s POSIX gaps and the large legacy `std` patch
set; (b) a `std`-compatible shim over kernel/module services, rejected because
it lacks processes, threads, signals and per-process file descriptors and would
not be the real `std`; (c) no `std`, rejected because it makes the native
ecosystem unusable; (d) a rustc fork, rejected because the pinned nightly plus a
patched `rust-src` and `-Zbuild-std` sysroot is sufficient and cheaper to
maintain.

**Consequences**: the native libc spec (`docs/spec/native-libc.md`) and POSIX
profile become interface contracts; the native target is distinct from the
kernel target (`target_os = "none"`) and from the compat target; `std` is
shipped as a pinned, patched-`rust-src` `-Zbuild-std` sysroot; the native libc,
process/cell entry and POSIX surface are M9/M9.5 work. ADR-0018 makes
`rustc_codegen_gcc` primary: the std claim is not load-bearing for userland
until `std` builds and runs on n32 under that backend, or ADR-0018 is amended to
make LLVM primary for userland and GCC the parallel portability backend. The
IRIX 6.5 `std` remains private/local because its generated libc bindings derive
from the SGI sysroot (ADR-0001, ADR-0009).
