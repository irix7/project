# Dynamic-first smoke harness

The smoke harness's first proof is a dynamically linked hello: the cross links
`oracle/hello.c` against the captured sysroot's `crt1.o`, `libc.so` and
`libm.so`, `iris-ci` ships it to the guest, and the harness diffs stdout. IRIX
6.5 ships no static libc — its nonshared libc archives are tagged `noship` in
the maintenance install database — and no nonshared `crt1.o`, so dynamic is
the only shipping link model; `dso(5)` calls `-non_shared` outmoded, and GCC's
`-static` has been broken on IRIX 6.5 for the same missing files. The GCC IRIX
port defaults to `-call_shared` and links SGI's `crt1.o`, so the proof
exercises the driver specs, startfiles and libc the rebuild depends on; the
baseline cross was validated this way while deciding, running on the guest
with the MIPSpro oracle's output.

The static proof is deferred, not dropped: the rebuilt runtime (#9) already
carries it, and the tree's libc Makefile builds the nonshared archives that
will seed it.

**Considered Options**: a freestanding `_start`-only static hello (rejected as
the first proof — it runs, but bypasses libc, the headers, the startfiles and
the sysroot, which is what the harness exists to test; direct `syscall`
instructions are outside SGI's ABI and no later milestone links that way);
vendoring a static libc from SGUG/NewEOE (rejected — no such artefact exists:
IRIX CE/NewEOE ship MIT-licensed utilities but no libc, and the only public
static libc source is SGI's tree, which ADR-0001 forbids publishing and #9/#14
will rebuild anyway).

**Consequences**: the harness's compiler must be configured against the
captured sysroot (`build-toolchain.sh --sysroot`, as #6's `IRIX_SYSROOT`
criterion says); #5 and #6 promise the dynamic path only; the threaded
follow-up (#18) needs `/usr/lib/libpthread.so` and `/usr/lib32/libpthread.so`
added to `sysroot.files` before its `-lpthread` link resolves.
