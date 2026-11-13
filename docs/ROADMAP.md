# Roadmap

Use one reviewable PR from validated `main` at a time. Each milestone issue records its acceptance criteria and prerequisites. The names and numbers on older local branches identify the prototype sequence. Promote their deterministic code and evidence in dependency order.

| Milestone issue | Acceptance | Depends on |
| --- | --- | --- |
| [01 Bootstrap #1](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/1) | Project packaging and lightweight CI published in [PR #2](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/2). | Complete |
| [02 Environment Doctor #3](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/3) | Offline probes published in [PR #13](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/13). | Complete |
| [03 Controller, evidence and provenance #4](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/4) | Ordered RTL manifests, fixed ORFS stages and recovery, and artifact-linked evidence published in [PR #14](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/14). | Complete |
| [04 Read-only MCP #5](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/5) | [Local stdio server and client](MCP.md) with discovery, scoped invocation, and structured denial. | 03 |
| [05 Offline knowledge and retrieval #6](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/6) | Evaluate local hybrid retrieval against BM25 using cited evidence and abstention cases. | 03, 04 |
| [06 Gateway and supervisor #7](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/7) | Demonstrate typed, resumable read-only diagnosis with optional inference. | 04, 05 |
| [07 Timing and physical diagnosis #8](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/8) | Compare rule-based and model-assisted diagnoses of reference timing failures with path and stage evidence. | 03, 06 |
| [08 Budgeted optimization #9](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/9) | Execute validated candidates and compare measured PPA and feasible frontiers. | 03, 06, 07 |
| [09 Verification and acceptance #10](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/10) | Evaluate configured RTL and physical checks with artifact-linked evidence. | 03, 08 |
| [10 Larger designs and OpenTitan prerequisites #11](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/11) | Demonstrate a medium-complexity design, then assess pinned native metadata, IP blocks, partition abstracts, and hard IP physical views before hierarchical experiments. | 09 |
| [11 Telemetry and benchmark reporting #12](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/12) | Reproduce evidence-backed reports and expose structured run telemetry. | 03, 10 |
