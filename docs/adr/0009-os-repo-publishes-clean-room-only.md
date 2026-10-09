# The OS repo publishes clean-room code only

`irix7/os` is public, but it only ever publishes clean-room reimplementations. The transcription reads the private `irix6` source tree to produce a behavioural specification — interfaces, symbol names, structure layouts, documented semantics — and the Rust committed to the public repo is written from that specification, not from the source text. No SGI-derived expression reaches the public repo: no copied code, no copied comments, no verbatim symbol or structure-name lists lifted from the tree. ADR-0001's rule — nothing SGI-derived is published — still holds; this decision extends its scope by designating the one new publishable artefact and the method that keeps it clean.

**Considered Options**:

- Publishing the transcription directly (rejected — it is derivative of the licence-restricted SGI tree, so it stays private beside `irix6` and `reference`).
- Keeping the OS repo private (rejected — the organisation's stated mission is to publish the Rust OS for other agents and researchers to discover and use).
- Clean-room reimplementation, public (chosen).

**Consequences**:

- The transcription specification stays private; only the Rust written against it is public.
- Clean-room slows the work — behaviour must be stated in the specification before code exists — but it is what makes the end goal publishable.
