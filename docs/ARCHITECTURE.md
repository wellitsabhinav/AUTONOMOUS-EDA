# Architecture

Adapters turn authoritative design metadata into an ordered manifest. The planner emits explicit stage dependencies, tool targets, required artifacts, and validation rules. The executor invokes fixed tool arguments, records evidence, and validates outputs before checkpointing. Reports carry measured metrics and independent gate states. Optimization and model providers consume those observations through typed interfaces and validated configuration changes.

ORFS owns physical algorithms and platform-specific build logic. OpenROAD provides placement and routing, OpenSTA handles timing, OpenDB supplies physical database access, and KLayout handles stream-out and applicable verification. See the [ORFS tutorial](https://openroad-flow-scripts.readthedocs.io/en/latest/tutorials/FlowTutorial.html).

The optional [local MCP interface](MCP.md) projects recorded schema 1 evidence through a fixed read-only tool catalog. It uses operator-approved experiment IDs, a bounded read worker, and stdio transport.

For OpenTitan, resolve the native build metadata at a pinned revision, capture the resulting EDAM file order and resolved generator outputs, and correlate it with TopGen HJSON for hierarchy, clock, and reset intent. Keep generated and upstream RTL intact. Track LEF, timing, GDS, power, and LVS views for SRAM, ROM, RRAM, AST, and IO before accepting an Earl Grey 2 chip-level plan. The public [Earl Grey 2 documentation](https://opentitan.org/book/hw/top_earlgrey/) describes its relationship to the first Earl Grey generation and the role of TopGen.
