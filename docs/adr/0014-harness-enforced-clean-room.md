# The harness enforces clean-room by structural split and verification

The clean-room boundary of ADR-0009 is enforced mechanically by the agent harness, not by convention: the harness keeps two agent populations apart — dirty readers that see the private SGI source and emit a behavioural specification, and clean writers that see only that specification and emit Rust — and it verifies the Rust contains no SGI-derived expression before it reaches `irix7/os`. The transcription itself is hybrid: automated parse-to-AST for the mechanical parts, and agents abstracting behaviour for the semantic parts. The clean writers then write the Rust from that specification.

**Consequences**: anything mechanically derived from SGI source (the parse tree, generated code, any specification that quotes source) stays private; only the Rust written from the abstracted specification is public. The structural split is the guarantee; verification is the backstop.
