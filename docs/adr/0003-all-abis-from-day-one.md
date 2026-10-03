# All three ABIs from day one

The toolchain emits big-endian o32, n32 and n64 from the start, although the Indy's default environment is o32 (`OBJECT_STYLE=32_M2`). The stock rebuild includes products built in each ABI, and multilib layout is a decision to make when the toolchain is configured, not to retrofit later.
