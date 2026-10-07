# Stock rebuild inventory

Checklist of every component of the IRIX 6.5.7m product set, tracked against
three questions for the rebuild:

- **Source** — does the source tree carry buildable source? (`full` / `stub` / `none`)
- **Decompiled** — has a binary-only component been decompiled to C?
- **Rebuild** — does it currently compile (native MIPSpro ground truth, or GCC 16.2)?

Legend: `✓` done, `~` in progress, `·` not started, `✗` blocked.

## Source isms

| Ism | Contents | Source | Rebuild |
| --- | --- | --- | --- |
| kernel + system libraries + commands | uts, libc/libm, cmd | full | ✓ kernel boots (IP22 N32) |
| userland | eoe commands, libraries | full | · not attempted |
| firmware | PROM, sash, ide, symmon | full* | ~ partial |
| graphics register headers | MGRAS/GR2/NG1 chip register maps | **missing** | ✗ blocks graphics drivers |

\* firmware source is complete, but its graphics drivers `#include` the
graphics register headers, which live in a separate SGI source ism we do not
have.

## Binary-only components (shipped as objects; source is a stub)

The kernel links `*stubs.a` for these; the real objects ship binary-only.

### Kernel — graphics

| Module | Source | Decompiled | Rebuild |
| --- | --- | --- | --- |
| gfx framework | stub | ✓ 56 funcs | · |
| gr2 (Express) | stub | ✓ 120 funcs | · |
| mgras (O2 CRM) | stub | ✓ 259 funcs | · |
| ng1 (Newport) | stub | ✓ 101 funcs | · |

### Kernel — input / audio / dmedia

| Module | Source | Decompiled | Rebuild |
| --- | --- | --- | --- |
| input/textport (htport, tport, idev, shmiq, pckbd, qcntl, pckeyboard, pcmouse) | stub | ✓ | · |
| audio (kdsp, audutil, a2_dd, rrm, xconn) | stub | ✓ | · |
| dmedia kernel (dms.a, vino.o, kdsp.a) | stub | ✓ | · |

### Userland — graphics

| Module | Source | Decompiled | Rebuild |
| --- | --- | --- | --- |
| libGLcore.so (N32/o32) | none | ✓ 3,232 funcs | · |
| libGL / libgl (N32/o32) | none | ✓ | · |
| Xsgi (X server + Newport DDX) | none | ✓ 3,890 funcs | · |
| gfxinit, setmon, hyperpipeinfo | none | ✓ | · |

### Userland — dmedia

| Module | Source | Decompiled | Rebuild |
| --- | --- | --- | --- |
| libdmedia, moviefile, image converters (100+ files) | none | ✗ out of scope so far | · |

## Recompilation status

| Layer | Status | Notes |
| --- | --- | --- |
| kernel | ✓ | native MIPSpro boots to multi-user; GCC 16.2 port is issue #39 |
| libc + libm | ✓ | GCC 16.2 cross, o32 nonshared (issue #9) |
| firmware libsc/libsk/libsl | ✓ | native MIPSpro |
| firmware sash | ✓ | native MIPSpro |
| firmware symmon / ide | ~ | compile; link on driver symbols |
| firmware PROM | ~ | all non-graphics objects compile; link blocked by missing register headers |
| userland eoe | · | not attempted |
| decompiled graphics/audio as source | · | dumps only, not yet buildable |

## Source-restoration fixes (native build)

1. smake `$(VAR)SUFFIX=` idiom is a no-op in the shipped smake — stripped the
   cpuboard prefixes across 248 Makefiles.
2. sash `-coff -32` → ELF path (7.3 ld dropped `-coff`).
3. libsk/libsc/libsl archive rule dropped objects → always archive.
4. Header farm populated with cross-tree kernel headers.

## Blockers

- [ ] Graphics register headers (MGRAS RE4/TE1/TR1/PP1, GR2, NG1) — in a
      separate SGI source ism, not on any ISO we hold; reconstruct from
      decompiled offsets + source usage + hardware docs.
- [ ] dmedia userland libraries (libdmedia, moviefile, converters) — undecompiled.
- [ ] symmon / ide / PROM remaining driver symbols (SCSI/serial/audio).
- [ ] userland eoe native build (not yet attempted).
