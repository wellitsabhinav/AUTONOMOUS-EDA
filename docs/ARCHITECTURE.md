# Architecture

The [verification evaluator](VERIFICATION_AND_ACCEPTANCE.md) combines schema 1 checkpoints with separately versioned attempts. Native RTL tools receive staged manifest inputs. The pinned OpenSTA probe receives verified final physical inputs. Profiles use independent gate states and objective bounds. MCP exposes a read-only summary from approved archives.

Adapters turn authoritative design metadata into an ordered manifest. The planner emits explicit stage dependencies, tool targets, required artifacts, and validation rules. The executor invokes fixed tool arguments, records evidence, and validates outputs before checkpointing. Reports carry measured metrics and independent gate states. Optimization and model providers consume those observations through typed interfaces and validated configuration changes.

ORFS owns physical algorithms and platform-specific build logic. OpenROAD provides placement and routing, OpenSTA handles timing, OpenDB supplies physical database access, and KLayout handles stream-out and applicable verification. See the [ORFS tutorial](https://openroad-flow-scripts.readthedocs.io/en/latest/tutorials/FlowTutorial.html).

The optional [local MCP interface](MCP.md) projects recorded schema 1 evidence through a fixed read-only tool catalog. It uses operator-approved experiment IDs, a bounded read worker, and stdio transport.

The [AI supervisor](AI_SUPERVISOR.md) reads the same approved evidence and uses the [knowledge index](RAG_ARCHITECTURE.md) for scoped technical citations. It records typed transitions in a separate SQLite journal. The [model gateway](MODEL_GATEWAY.md) supplies bounded interpretation choices. The supervisor checks evidence integrity and choice IDs before publishing a structured explanation.

The [diagnosis engine](TIMING_DIAGNOSIS.md) parses verified timing reports, stage metrics and SDC from the bounded evidence snapshot. It reconciles path arithmetic, compares stage extrema and matching endpoint checks, and emits cited observations, calculations, hypotheses and measurement requests. The supervisor consumes the [structured diagnosis](DIAGNOSIS_SCHEMA.md) and can use a model to prioritize its existing findings.

The [optimizer](OPTIMIZATION.md) binds deterministic or local-model choices to an approved finite plan. Its campaign journal reserves execution and tracks resource limits while the controller owns physical stages and verification. The evaluator checks comparable measured outcomes and gate-qualified frontiers. The [artifact renderer](EXAMPLE_GALLERY.md) projects verified run and campaign evidence into traceable KLayout and Agg PNGs.

For OpenTitan, resolve the native build metadata at a pinned revision, capture the resulting EDAM file order and resolved generator outputs, and correlate it with TopGen HJSON for hierarchy, clock, and reset intent. Keep generated and upstream RTL intact. Track LEF, timing, GDS, power, and LVS views for SRAM, ROM, RRAM, AST, and IO before accepting an Earl Grey 2 chip-level plan. The public [Earl Grey 2 documentation](https://opentitan.org/book/hw/top_earlgrey/) describes its relationship to the first Earl Grey generation and the role of TopGen.
