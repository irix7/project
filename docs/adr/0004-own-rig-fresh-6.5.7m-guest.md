# Own rig with a fresh IRIX 6.5.7m guest

The project builds an independent rig beside the sibling `sgi-mame` session: own emulator checkout and binary, own socket, disks and NVRAM, and a fresh IRIX 6.5.7m install from the media rather than a copy of the existing 6.5.22m guest. The rebuild target is 6.5.7m, so the execution environment — and the sysroot captured from it — match the tree under rebuild, and the sibling session's rig is never disturbed.

**Consequences**: slower to first light than copying the working image; MIPSpro 7.3 and CEE 7.4 are installed into the fresh guest to serve as the oracle.
