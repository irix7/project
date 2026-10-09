# IRIX 6.5 -> modern open-source equivalents catalogue

Reference for ADR-0017. Classification: **same software now open-sourced** / **faithful reimplementation** / **no equivalent**.

## Desktop & windowing

| IRIX component | Modern equivalent | Class |
| --- | --- | --- |
| CDE | [CDE](https://sourceforge.net/projects/cdesktopenv/) — LGPL-2.0 | same software now open-sourced |
| Motif | [OpenMotif](https://sourceforge.net/projects/motif/) — LGPL-2.1 | same software now open-sourced |
| mwm | mwm (OpenMotif) — LGPL-2.1 | same software now open-sourced |
| 4dwm | [5Dwm / MaXX](https://github.com/maxxdesktop) | faithful reimplementation |
| Indigo Magic Desktop | MaXX Interactive Desktop | faithful reimplementation |
| ToolTalk | [OpenSolaris ToolTalk](https://github.com/illumos/illumos-gate) — CDDL | same software now open-sourced |
| dtappbuilder / UIL | OpenMotif | same software now open-sourced |

## X11 & windowing

| IRIX component | Modern equivalent | Class |
| --- | --- | --- |
| X Window System | [X.Org](https://www.x.org/) — MIT | same software now open-sourced |
| X server (XSGI) | [X.Org Server](https://gitlab.freedesktop.org/xorg/xserver) — MIT | same software now open-sourced |
| GLX | Mesa/X.Org — MIT | same software now open-sourced |
| X font server | X.Org `xfs` — MIT | same software now open-sourced |

## Graphics libraries

| IRIX component | Modern equivalent | Class |
| --- | --- | --- |
| IRIS GL | — | no equivalent |
| OpenGL | [Mesa](https://www.mesa3d.org/) — MIT | same API / faithful impl |
| GLU | Mesa GLU — SGI FSL-B | same software now open-sourced |
| Open Inventor | [SGI Open Inventor](https://github.com/aumuell/open-inventor) — LGPL-2.1 | same software now open-sourced |
| Open Inventor (alt) | [Coin3D](https://github.com/coin3d/coin) — BSD-3-Clause | faithful reimplementation |
| OpenGL Performer | [OpenSceneGraph](https://github.com/openscenegraph/OpenSceneGraph) | faithful reimplementation |
| ImageVision Library | — | no equivalent |
| OpenGL Volumizer | — | no equivalent |

## Compiler & toolchain

| IRIX component | Modern equivalent | Class |
| --- | --- | --- |
| MIPSpro C/C++/F77/F90 | — | no equivalent |
| MIPSpro / IDO | — | no equivalent |
| MIPS codegen | [GCC](https://gcc.gnu.org/) / [binutils](https://www.gnu.org/software/binutils/) — GPL | no equivalent (nearest tool) |
| MIPSpro C++ runtime (libC) | libstdc++ / libc++ | faithful reimplementation (role) |
| dbx / cvd | [gdb](https://www.gnu.org/software/gdb/) — GPL-3.0 | no equivalent |

## Shell & SVR4 userland

| IRIX component | Modern equivalent | Class |
| --- | --- | --- |
| /bin/sh | [Heirloom sh](https://heirloom.sourceforge.net/sh.html) — CDDL | same lineage |
| /bin/ksh | [ksh93u+m](https://github.com/ksh93/ksh) — EPL-2.0 | same software now open-sourced |
| csh/tcsh | [tcsh](https://github.com/tcsh-org/tcsh) — BSD | same software now open-sourced |
| SVR4 utilities | [Heirloom Toolchest](https://heirloom.sourceforge.net/tools.html) — CDDL | same lineage |
| awk | Heirloom awk — CDDL | same lineage |
| vi/ex | [nvi](https://sites.google.com/a/bostic.com/keithbostic/vi) — BSD | same lineage |
| lex/yacc | flex — BSD / bison — GPL | faithful reimplementation |
| SCCS | Heirloom SCCS — CDDL | same lineage |
| LP spooler | OpenSolaris LP — CDDL / CUPS — Apache-2.0 | same lineage / functional successor |

## C library & runtime

| IRIX component | Modern equivalent | Class |
| --- | --- | --- |
| libc | [illumos libc](https://github.com/illumos/illumos-gate) — CDDL | no equivalent (nearest family) |
| libm | fdlibm / glibc libm | faithful reimplementation |
| libC / C++ runtime | libstdc++ / libc++ | faithful reimplementation (role) |

## Networking

| IRIX component | Modern equivalent | Class |
| --- | --- | --- |
| sendmail | [sendmail](https://www.proofpoint.com/us/products/email-protection/open-source-email-solution) | same software now open-sourced |
| BIND | [ISC BIND](https://www.isc.org/bind/) — MPL-2.0 | same software now open-sourced |
| inetd | OpenBSD inetd — BSD / xinetd | faithful reimplementation |
| rpcbind | [rpcbind](https://sourceforge.net/projects/rpcbind/) — BSD | same software now open-sourced |
| NFS | Linux nfs-utils — GPL-2.0 / OpenSolaris — CDDL | faithful reimplementation |
| NIS | OpenSolaris NIS — CDDL | same lineage |
| xntpd | [NTP](https://www.ntp.org/) | same software now open-sourced |
| SNMP | [Net-SNMP](https://www.net-snmp.org/) — BSD | faithful reimplementation |
| tcp_wrappers | tcp_wrappers — BSD | same software now open-sourced |
| rsh/rlogin | netkit / OpenBSD — BSD | same software now open-sourced |
| PPP | [ppp](https://github.com/ppp-project/ppp) — BSD | same software now open-sourced |
| DHCP | [ISC DHCP](https://www.isc.org/dhcp/) — MPL-2.0 | same software now open-sourced |

## Filesystem

| IRIX component | Modern equivalent | Class |
| --- | --- | --- |
| XFS | [Linux XFS](https://git.kernel.org/pub/scm/fs/xfs/xfs-linux.git) — GPL-2.0 | same software now open-sourced |
| EFS | — | no equivalent |
| CXFS | — | no equivalent |

## Fonts & system services

| IRIX component | Modern equivalent | Class |
| --- | --- | --- |
| X font utils | X.Org font utils — MIT | same software now open-sourced |
| fontconfig/FreeType | fontconfig — MIT / FreeType — FTL | faithful reimplementation (role) |
| SGI screen fonts | — | no equivalent |
| Performance Co-Pilot | [PCP](https://pcp.io/) — LGPL-2.1 | same software now open-sourced |
| fam | [fam](https://github.com/steinarb/fam) — LGPL-2.1 | same software now open-sourced |
| syslogd | OpenBSD syslogd — BSD | faithful reimplementation |
| cron | Vixie/ISC cron — BSD | same software now open-sourced |

## Other

| IRIX component | Modern equivalent | Class |
| --- | --- | --- |
| dmedia libs | FFmpeg / GStreamer | no equivalent (functional successor) |
| Audio Library (AL) | OpenAL — LGPL | no equivalent (functional successor) |
| IRIS IM | — | no equivalent |
| Cosmo Player / VRML2 | FreeWRL — GPL / OpenVRML — LGPL | faithful reimplementation |
| InPerson / Showcase | — | no equivalent |
| sprocs / ulist | — | no equivalent (reimplemented as first-class IRIX 7 services) |

## Observations

- Strongest "same software now open-sourced": XFS, CDE, OpenMotif/mwm, Open Inventor, PCP, fam, X11/X.Org, ksh93, Heirloom SVR4 tools.
- Largest "no equivalent" block: MIPSpro/IDO (nothing open is the same compiler; GCC targets the same ABI but is a different compiler), IRIS GL, ImageVision, dmedia, AL, and the IRIX-specific parallel/media subsystems.
