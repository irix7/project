# IRIX 7 reimplements everything on the IRIX discs, booting from the factory PROM

IRIX 7 reimplements, clean-room in Rust, everything that ships on the IRIX discs — kernel, libc and the whole userland — because no SGI binaries or source may ship (ADR-0001, ADR-0009). It boots from the factory PROM so the OS is easy to try on existing Indy hardware; a Rust reimplementation of the PROM itself is a stretch goal, not a prerequisite. The legacy ABI layer therefore runs a *user's* existing third-party IRIX 6.5 binaries; SGI's own products are reimplemented in Rust, not carried forward as binaries.

**Consequences**: the shipped IRIX 7 contains zero SGI code, binaries or source. The factory PROM is assumed present (it is on the machine already, not something we redistribute), and the OS is reached through it.
