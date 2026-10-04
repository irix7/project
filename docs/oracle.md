# The oracle

The guest's native MIPSpro build is the project's oracle: the ground truth for
what correct IRIX output looks like while the new GCC 16.2 toolchain is brought
up (ADR-0005). Issue #3 installs SGI's compilers in the fresh 6.5.7m guest,
builds a reference program with them, and captures the guest's headers,
startfiles and libraries as a sysroot on shared storage.

Nothing captured here is ever published (ADR-0001): the sysroot, objects,
binaries and logs are SGI material and stay under `$RIG_ORACLE_DIR`
(default `/mnt/europa/sgi-toolchain-scratch/rig/oracle`). Only the scripts are
in the repo.

## Media

Three ISOs, copied into `$RIG_MEDIA_DIR` beside the install media and never
published:

| ISO                                           | Supplies |
|-----------------------------------------------|----------|
| `IRIX 6.5 Development Libraries June 1998.iso` | headers (`irix_dev.sw.headers`), crt startfiles and dev libs (`dev.sw.lib`) |
| `MIPSpro All-Compiler CD May 1999.iso`         | MIPSpro 7.3: `compiler_dev`, `c_dev`, `c_fe` |
| `Compiler Execution Environment 7.4.iso`       | 7.4 runtime libraries (`compiler_eoe`) |

Set `IRIX_ORACLE_MEDIA_SOURCE` to a directory to have the script copy missing
ISOs in. The dedicated MIPSpro C 7.3 disc and the IRIX 6.5 Development
Foundation disc are an earlier 7.2.1 path and are not needed.

`iris.toml` also carries a 64 MB scratch LUN at SCSI ID 2
(`$RIG_DIR/scratch.raw`). `iris-ci put` and `get` stage files through it as
`/dev/rdsk/dks0d2s0`; without it the capture cannot move files in or out.

## Licensing

MIPSpro checks out FLEXlm features (`cc`, ...) at compile time. A fresh guest
has only WorkShop's evaluation licence, so `cc` refuses to run its phases. Two
local options, neither shipped in this repo:

- `IRIX_MIPSPRO_DRIVER` — a locally prepared 7.3 driver with the licence check
  removed. `scripts/rig/oracle.sh patch` installs it at
  `/usr/lib32/cmplrs/driver`; the default path is
  `$RIG_ORACLE_DIR/mipspro-7.3-n32-driver`.
- `IRIX_MIPSPRO_LICENSE` — a valid `/var/flexlm/license.dat` with the `cc`
  feature.

With neither, `patch` fails with a clear message and `cc` cannot be the oracle.

## Bring-up

```sh
scripts/rig/oracle.sh install   # inst the three discs (resumable, markers)
scripts/rig/oracle.sh patch     # local driver or licence
scripts/rig/oracle.sh capture   # build hello, pull objects and sysroot
scripts/rig/oracle.sh all       # all three
scripts/rig/oracle.sh status    # markers and the captured tree
```

`install` drives `inst` over the serial console with `oracle-driver.py` (the
same `Rig` class as the issue #2 install): it loads each disc with
`cdrom-load`, mounts it at `/CDROM`, scans `/CDROM/dist`, `keep *`s to make the
install surgical, installs the named products with `rulesoverride` on, then
`go` and `quit`. Each completed disc is marked under `$RIG_STATE_DIR`; `--force`
redoes them.

`capture` runs `oracle/hello.c` through `cc` twice — explicit o32 and n32,
because MIPSpro 7.3's default ABI on this guest is n32 — keeps the binaries,
objects, assembly and `-v` build transcripts, and records `uname -a`,
`cc -version` and `versions` output in `environment.txt`. The sysroot is the
file list in `scripts/rig/sysroot.files` tarred in the guest and pulled back
with `iris-ci get`.

## Captured tree

| Path | What it is |
|------|------------|
| `environment.txt` | `uname -a`, `cc -version`, `versions` proof of the installed products |
| `hello.o32`, `hello.o32.o`, `hello.o32.s`, `hello.o32.build.log`, `hello.o32.output` | o32 binary, object, assembly, transcript and recorded output |
| `hello.n32`, `hello.n32.o`, `hello.n32.s`, `hello.n32.build.log`, `hello.n32.output` | the same for n32 |
| `sysroot.tar.gz`, `sysroot/`, `sysroot.manifest` | headers, startfiles and libc/libm for o32, n32 and n64 |
| `sysroot.sha256` | per-file checksums of the extracted sysroot tree |
| `manifest.sha256` | checksums of everything above |
| `mipspro-7.3-n32-driver` | locally patched driver, when one is in use (never committed) |

The sysroot manifest is deliberately bounded: `/usr/include` plus the crt
objects, `libc`/`libm` link libraries and their `/lib*` runtime DSOs. The
guest's `/usr/lib*` trees also hold hundreds of product libraries; those are
not part of this capture.

## MIPSpro quirks worth remembering

- `cc` on this guest defaults to **n32**, so every ABI is named explicitly.
- `-S` ignores `-o` and always writes `<basename>.s`.
- A link step in a directory removes stale `<basename>.o` files, so the o32
  and n32 builds each get their own build directory and the link runs before
  the object/assembly steps.
- New binaries need `rehash` in the guest's csh before the shell will find
  them.
- The licence bypass makes `cc` print its "requires a license password" banner
  even though the phases run; the capture's build logs contain it.
