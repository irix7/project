# The transcription spec is layered; clean-room verification is a similarity screen

The clean-room specification that the harness's dirty readers emit is layered: a machine-readable semantic IR — the SOURCE→AST→RUST AST abstracted to behaviour — with per-subsystem structured documents derived from it for the clean writers to read. The harness's verification of clean-side output is a token/structure similarity-and-diff screen against the private source; flagged matches go to a human before anything reaches `irix7/os`.

**Consequences**: the IR is the single machine-checkable source of truth; the structured documents are the human/agent-readable view; the similarity screen is the backstop behind the structural split of ADR-0014.
