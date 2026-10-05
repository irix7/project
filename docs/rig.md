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
| `state/build-iris.receipt` | the emulator build's inputs and outputs (repo, commits, features, Rust identity, binary paths) |

## Prerequisites

- The four install discs copied into `media/` (install tools/overlays 1,
  foundation 1, foundation 2, overlays 2). Never publish these.
- GNU nix with the flake's `rig` devshell for the host C toolchain and
  libraries; `nix develop .#rig --command bash` is the intended shell.
- Rust: `build-iris.sh` provisions a rig-local nightly via rustup on first use.

## The emulator build receipt

`build-iris.sh` records a receipt at `state/build-iris.receipt` naming the
upstream repo, the requested and resolved commits, the `lightning` feature
and the rig-local `rustc -V`/`cargo -V` identity, together with the produced
binary paths. The binary is reused only while every one of those still
matches and both binaries are executable; a changed pin, feature or nightly
rebuilds. The receipt is written after a successful build, so a failed or
partial build never counts as current.

A dirty checkout is refused before any forced checkout (`git status
--porcelain`), so local emulator changes are never discarded implicitly.
`--force` means rebuild; discarding changes needs an explicit
`--force-checkout`:

```sh
scripts/rig/build-iris.sh                   # reuse a receipted build
scripts/rig/build-iris.sh --force           # rebuild, keep local changes
scripts/rig/build-iris.sh --force --force-checkout  # discard, then rebuild
```

The receipt logic keeps its host-only test (a fake local git origin, fake
nix and a fake rig-local Rust toolchain; nothing is fetched or compiled):

```sh
python3 scripts/rig/test-build-iris.py
```

## State markers and generations

Provisioning and oracle phases record completion in `state/<name>.done`
through `scripts/rig/rig-state.py`. A marker is not a timestamp: it is bound
to a *generation* derived from the two things that define the guest under
test.

- The boot disk `$RIG_DISK`: its size, device, inode and a bounded (64 KiB)
  digest of the volume header at the start of the image. The file timestamp is
  deliberately excluded — the emulator writes inside the partitions during an
  install, and that must not invalidate the install's own phase markers. A
  replaced disk differs in inode and header digest; a removed disk is the
  distinct `absent` identity.
- The rig config `$RIG_CONFIG`: a digest of its bytes, so any edit invalidates
  dependent markers.

`marked NAME` is true only when the marker exists *and* its generation matches
the current disk and config; `status NAME` prints `done ...`, `pending`,
`stale generation (marker ..., disk/config ...)` or `legacy timestamp-only
marker ...`. The shell helpers `rig_state` and `rig_state_marked` in `lib.sh`
wrap the CLI; `install-driver.py`, `provision-guest.sh`, `oracle.sh` and
`status.sh` all use them.

The marker format is three lines:

```
generation: <sha256 hex>
created: <local ISO timestamp>
postcondition: <the claim the marker makes>
```

Writes are atomic (a same-directory temp file followed by `os.replace`), so a
reader never sees a partial marker and a crash leaves the previous marker
intact. `mark` is only called after the postcondition it names has been
checked, and a phase with no marker is simply pending.

Markers written before generation binding (a bare timestamp) and markers for a
different disk or config do not certify anything: the phase re-runs.
Replacing the boot disk or editing `iris.toml` therefore invalidates every
dependent phase marker; `--fresh` remains the explicit way to drop all state
and start over.

The host-only marker tests run against temporary disk/config/state trees:

```sh
python3 scripts/rig/test-rig-state.py
```

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
stream by parsing the menu's option number, and fails rather than defaulting
if the menu offers no maintenance option. The verify phase refuses to declare
the phase done unless `versions eoe` names 6.5.7m: `logs/evidence.txt` records
that version proof alongside `uname -a` and `hinv`, only after the assertion.
An interrupted verify resumes from a login prompt, a logged-in shell or the
PROM.

The install's `go` transcript is accumulated and classified as a whole:
`ERROR:`/`Installations and removals failed` and unresolved conflicts take
precedence over the success line, and the default allowlist of harmless
messages is empty (an entry needs a cited transcript proving the exact line
harmless). A required install disc that presents no distribution tree at all
fails the phase; a miss on `/CDROM/dist` is tolerated only when
`/CDROM/dist/unbundled` scanned.

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
process untouched for inspection. A verified PID that survives the signals is
refused the same way and never reported stopped — with or without a socket —
so `--fresh` cannot delete a disk under a live emulator. A zombie is treated
as dead.

`--fresh` stops the guest first, then requires quiescence
(`rig_require_stopped`: no socket present and no live recorded pid) before it
deletes `state/`, the disk or NVRAM; if it cannot verify, it deletes nothing
and dies with the reason.
