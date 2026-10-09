# Modern open-source equivalents are design references, not shipped code

Where a modern open-source version of an IRIX component exists — the same software now open-sourced, or a faithful reimplementation (e.g. CDE, OpenMotif, a reconstructed 4dwm) — it is used as a design and behavioural reference, and the component is still reimplemented in Rust. "Everything in Rust" (ADR-0013) holds; the open-source source is simply a better reference than SGI decompilation, and being open it does not require the clean-room machinery of ADR-0014. Reimplementing the design rather than translating the code keeps the result GPL-3.0, not a derivative of the reference's licence.

**Consequences**: the catalogue of components with such equivalents becomes its own inventory; those components skip clean-room (the reference is public) but still produce Rust.
