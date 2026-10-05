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
IRIS_SOCKET=$RIG_DIR/iris.sock ./iris/target/release/iris-ci \
    --socket "$RIG_DIR/iris.sock" run "uname -a"
```

The scripts export `IRIS_SOCKET` and pass `--socket`, so the same shell works
for `iris-ci login`, `run`, `put`, `get` and the fresh-guest smoke harness that
follows in issue #5.

`iris-ci put`/`get` move files through the SCSI 2 scratch LUN (`scratch.raw`).
The MIPSpro oracle install, reference build and sysroot capture build on them;
see `docs/oracle.md`.

## Socket identity

Every script and driver that speaks to the guest addresses the selected
socket explicitly, in both carriers: `--socket "$RIG_SOCKET"` in argv and
`IRIS_SOCKET="$RIG_SOCKET"` in the environment. iris-ci falls back to
`$IRIS_SOCKET` or the default `/tmp/iris.sock` when `--socket` is absent, so
either carrier alone is not enough. There are two call sites: lib.sh's `ic()`
wrapper (used by the shell scripts, smoke.sh and the hinv harnesses) and
install-driver.py's `Rig.run_ic` (used by its own verify phase and by
oracle-driver.py's ensure-shell login/boot). Manual invocations should spell
out both carriers as in the example above.

## Lifecycle and locking

One lock serialises every client that drives the guest:
`$RIG_GUEST_LOCK` (`$RIG_DIR/guest.lock`; override with `RIG_GUEST_LOCK` and
`RIG_GUEST_LOCK_TIMEOUT`, default 3600 seconds). `rig_with_guest_lock
COMMAND...` takes an `flock(2)` on that one file for the duration of COMMAND.

- Lifecycle scripts hold it coarsely for the whole operation: start-rig.sh,
  stop-rig.sh, provision-guest.sh and oracle.sh (every action, `status`
  included). smoke.sh, hinv-reference.sh and hinv-gcc.sh take it per guest
  transaction instead, so host-side preparation happens outside the lock but
  no two guest transactions interleave.
- Composition is strictly nested inside the one lock: there is no second lock
  and no lock ordering to get wrong. Re-entrancy is explicit: the holder
  exports `RIG_GUEST_LOCK_HELD=1`, and a nested `rig_with_guest_lock` runs its
  command directly instead of flocking the same file and deadlocking.
  Provision's internal start/stop calls and oracle's start call are the
  nested cases in practice.
- The wait is bounded: on timeout the caller dies with a message rather than
  hanging. A started emulator never inherits the lock fd, so the guest does
  not hold the lock for its lifetime.

Consequences: a stop waits for an in-flight smoke transaction, and a start
while another start is mid-flight waits, then observes the first's socket and
exits without creating a second instance on the same disk.

## Process ownership

`$RIG_PID_FILE` is never trusted on liveness alone. `rig_pid_is_ours PID`
requires `/proc/PID/exe` to resolve to `$RIG_IRIS` and the process cmdline to
carry this rig's `--config` path and `--ci`; a missing or unreadable process,
a non-numeric PID or any mismatch means "not ours". `stop-rig.sh` signals
only a verified PID (SIGTERM, then SIGKILL after a bounded wait) and otherwise
refuses with a clear message, leaving the socket, the pid file and the
process untouched for inspection. A zombie is treated as dead.

`--fresh` stops the guest first, then requires quiescence
(`rig_require_stopped`: no socket present and no live recorded pid) before it
deletes `state/`, the disk or NVRAM; if it cannot verify, it deletes nothing
and dies with the reason.
