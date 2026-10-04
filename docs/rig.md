# The rig

The independent IRIX 6.5.7m execution environment behind issue #2 and ADR-0004:
its own emulator checkout and binary, socket, disks, NVRAM and serial log,
running a fresh IRIX 6.5.7m install from the original media. The sibling
`sgi-mame` session is never touched; the rig owns `/tmp`-free paths under
`$IRIX_RIG_DIR` (default `/mnt/europa/sgi-toolchain-scratch/rig`).

MIPSpro 7.3 and CEE 7.4 as the build oracle are issue #3's scope, not this
ticket's.

## Layout

| Path                    | What it is |
|-------------------------|------------|
| `iris/`                 | the rig's own checkout of `techomancer/iris`, pinned in `build-iris.sh` |
| `iris.toml`             | config: own `ci_socket`, `nvram`, serial log, 20GB disk at SCSI 1, media changer at SCSI 4 |
| `iris.sock`             | CI control socket; never the default `/tmp/iris.sock` |
| `disks/irix65.raw`      | the guest's boot disk, sparse until the install writes to it |
| `scratch.raw`           | 64 MB CI scratch LUN at SCSI 2; `put`/`get` stage files through it as `/dev/rdsk/dks0d2s0` |
| `media/`                | the four IRIX 6.5.7 discs, in changer order, plus the oracle media |
| `oracle/`               | the captured MIPSpro oracle and sysroot (see `docs/oracle.md`); SGI material, never published |
| `logs/serial.log`       | every byte the guest writes to ttyd1 |
| `logs/driver.log`       | the bring-up driver's transcript and its own decisions |
| `logs/evidence.txt`     | `uname -a` and `hinv` captured over `iris-ci run` |
| `state/`                | per-phase done markers, so provisioning resumes rather than repeats |

## Prerequisites

- The four install discs copied into `media/` (install tools/overlays 1,
  foundation 1, foundation 2, overlays 2). Never publish these.
- GNU nix with the flake's `rig` devshell for the host C toolchain and
  libraries; `nix develop .#rig --command bash` is the intended shell.
- Rust: `build-iris.sh` provisions a rig-local nightly via rustup on first use.

## Bring-up

```sh
scripts/rig/provision-guest.sh          # build, partition, install, verify
scripts/rig/status.sh                   # what is done, what is running
tail -f "$RIG_LOG_DIR/serial.log"       # watch the guest console
```

`go` in the installer runs for one to two emulated hours. The script is
resumable: completed phases are skipped. A failed install needs `--fresh`,
which drops the state markers, NVRAM and disk image.

The installer asks whether to load the maintenance or feature stream. The
rebuild target is IRIX 6.5.7**m**, so the driver selects the maintenance
stream; `logs/evidence.txt` records `versions eoe` naming 6.5.7m alongside
`uname -a` and `hinv`.

## Day-to-day control

```sh
scripts/rig/start-rig.sh                # REX3 mapped, serial console, own socket
scripts/rig/stop-rig.sh
IRIS_SOCKET=$RIG_DIR/iris.sock ./iris/target/release/iris-ci run "uname -a"
```

The scripts export `IRIS_SOCKET`, so the same shell works for `iris-ci login`,
`run`, `put`, `get` and the fresh-guest smoke harness that follows in issue #5.

`iris-ci put`/`get` move files through the SCSI 2 scratch LUN (`scratch.raw`).
The MIPSpro oracle install, reference build and sysroot capture build on them;
see `docs/oracle.md`.
