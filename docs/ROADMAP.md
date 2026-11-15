# Roadmap

Use one reviewable PR from validated `main` at a time. Each milestone issue records its acceptance criteria and prerequisites. The names and numbers on older local branches identify the prototype sequence. Promote their deterministic code and evidence in dependency order.

| Milestone issue | Acceptance | Depends on |
| --- | --- | --- |
| [01 Bootstrap #1](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/1) | Project packaging and lightweight CI published in [PR #2](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/2). | Complete |
| [02 Environment Doctor #3](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/3) | Offline probes published in [PR #13](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/13). | Complete |
| [03 Controller, evidence and provenance #4](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/4) | Ordered RTL manifests, fixed ORFS stages and recovery, and artifact-linked evidence published in [PR #14](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/14). | Complete |
| [04 Read-only MCP #5](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/5) | [Local stdio server and client](MCP.md) published in [PR #15](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/15). | Complete |
| [05 Offline knowledge and retrieval #6](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/6) | [Local retrieval](KNOWLEDGE.md) published in [PR #16](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/16). | Complete |
| [06 Gateway and supervisor #7](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/7) | [Resumable read-only diagnosis](AI_SUPERVISOR.md) with [observed local inference results](AI_EVALUATION.md). | 04, 05 |
| [07 Timing and physical diagnosis #8](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/8) | [Offline path and stage diagnosis](TIMING_DIAGNOSIS.md) and [reference evaluation](DIAGNOSIS_EVALUATION.md) on the GCD and Ibex runs. | 03, 06 |
| [08 Budgeted optimization #9](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/9) | [Bounded candidate execution](OPTIMIZATION.md), [measured PPA and feasibility](OPTIMIZATION_EVALUATION.md), and [verified visual examples](EXAMPLE_GALLERY.md). | 03, 06, 07 |
| [09 Verification and acceptance #10](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/10) | [Evaluate configured RTL and physical checks](VERIFICATION_AND_ACCEPTANCE.md) with artifact-linked evidence. | 03, 08 |
| [09A SKY130 pad-ring assembly #20](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/20) | [Verify ICeWall placement](IO_PAD_RING.md) and qualify chip-level gates when pad GDS and electrical views are available. | 09 |
| [09D Technical manual and physical explorer #25](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/25) | Publish the [technical manual](../README.md) and [GCD walkthrough](tutorials/GCD_WALKTHROUGH.md) and validate the [physical workspace](PHYSICAL_WORKSPACE.md) with artifact-bound snapshots, an execution console and evidence-grounded diagnostics. | 09, 09A. Chip signoff is tracked in [09C #24](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/24). |
| [10 Larger designs and OpenTitan prerequisites #11](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/11) | Demonstrate a medium-complexity design, then assess pinned native metadata, IP blocks, partition abstracts, and hard IP physical views before hierarchical experiments. | 09 |
| [10A Hierarchical ingestion](tutorials/OPENTITAN_AES.md) | Resolve pinned FuseSoC EDAM, hash generation output, elaborate OpenTitan AES and publish structural readiness. | 09 |
| [10B Intermediate physical pilot](tutorials/OPENTITAN_AES.md#attempt-the-sky130hd-core-flow) | Bind translated AES into the deterministic Sky130HD controller, preserve attempts and publish available physical checkpoints and gates. | 10A |
| [10C Macro and partition readiness](OPENTITAN_READINESS.md) | Verify the available native DFFRAM views and inventory the pinned Earl Grey source and TopGen boundaries. | 10A, 10B |
| [10D Studio 2.0](PHYSICAL_WORKSPACE.md) | Inspect genuine GCD and AES checkpoints in a responsive browser workspace, with timing paths, verification, execution events and traceable PNG exports. | 10B, 09D |
| [11 Telemetry and benchmark reporting #12](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/12) | Reproduce evidence-backed reports and expose structured run telemetry. | 03, 10 |
