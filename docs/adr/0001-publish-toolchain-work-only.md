# Publish toolchain work only

The stock rebuild runs against a proprietary IRIX 6.5.7m source tree, so the public output is limited to the toolchain itself: GCC and binutils patches (GPL by inheritance, as with any GCC fork), build scripts and documentation. Nothing SGI or licence-restricted is ever published — no sysroot, headers, compiled binaries, disk images or tree excerpts. The rebuild deliverables stay private.

**Considered Options**: publishing the captured sysroot (rejected — it redistributes SGI headers and libraries); publishing boot images (rejected — licence-restricted UNIX code).
