# Static-first rebuilt runtime

The rebuilt libc and libm are proven by a fully static o32 binary: the tree's
own csu startfiles plus the rebuilt nonshared archives, with no SGI runtime
and no runtime linker. The dynamic model stays with SGI's captured libc for
now (ADR-0006). Dynamic linking against the *rebuilt* runtime is deferred:
IRIX ships its runtime linker `rld` binary-only, the tree's maths library has
no source in the 6.5.7m upload, and a shared rebuilt libc needs the
`so_locations`/exports machinery and a loader contract that the static proof
does not. The static boundary is explicit and documented in
`docs/runtime.md`: TLS off, LTO off, `-G 0`/`-mxgot` against the 64 KB o32
GOT, and zeroed rld symbols where `libcthread`'s DSO walk would ask the
loader.

**Considered Options**: rebuilding `libc.so.1` and rld now (rejected — rld is
pinned binary, no libm source exists, and the rebuilt loader is its own
milestone); linking the rebuilt static archive with SGI's dynamic `crt1.o`
(rejected — it mixes runtimes and reintroduces `PT_INTERP`, defeating the
proof).

**Consequences**: the smoke harness remains dynamic against the captured
sysroot; the runtime rebuild script carries the o32 boundary; n32 runtime
variants and the shared archive are later work (#10, #14), as is the native
bootstrap (#15). n64 is out of scope (ADR-0003 scope note).
