# IRIX 7 boots through an o32 ECOFF bootstrap into the n32 kernel

The Indy's ARCS PROM is a 32-bit o32 environment and accepts ECOFF images only, so the n32 ELF32 kernel is not loaded directly (ELF is rejected by the PROM). A minimal o32/ECOFF first stage performs the PROM handover — the romvec firmware calls, the system parameter block at 0x1000, the memory descriptors, and the console — and then hands a clean environment to the n32/MIPS III kernel, which never talks o32 to the firmware.

**Consequences**: the bootstrap is the only o32 piece; the n32 kernel is reached after the bootstrap has finished with the firmware. The bootstrap's implementation language is decided separately (it depends on the Rust codegen-backend question).
