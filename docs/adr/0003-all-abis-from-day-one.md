# All three ABIs from day one

The toolchain emits big-endian o32, n32 and n64 from the start, although the Indy's default environment is o32 (`OBJECT_STYLE=32_M2`). The stock rebuild includes products built in each ABI, and multilib layout is a decision to make when the toolchain is configured, not to retrofit later.

**Scope note (2026-10-05)**: the multilib still carries all three ABIs, but n64 is outside the project's acceptance scope. The IP22/R4400 guest is 32-bit and cannot execute n64, and no 64-bit target is planned, so n64 objects are not smoked, oracle-diffed or link-checked, no C++ n64 multilib is required, and full-tree acceptance records n64 products as exceptions. o32 and n32 remain the proven ABIs (issue #20).
