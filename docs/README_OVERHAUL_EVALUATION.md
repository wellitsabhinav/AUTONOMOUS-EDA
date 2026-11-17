# README handbook engineering receipt

The workstream started from `15bb972485993fa6098a3a23789d14c72024b5d2`, the validated Milestone 12 merge. [Tracking issue #44](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/44) · [Baseline checks](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/44044670089)

The root README now contains the installation and GCD reproduction, an illustrated seven-stage physical tutorial, STA and independent acceptance, OpenTitan AES, Studio, agent and MCP usage, bounded optimization, the CLI and a failure and recovery catalog. The handbook was authored in `codex/readme-technical-handbook` in its own worktree. After Milestone 12B merged, the workstream rebased to validated `f2f35531593f27b11ed2eafe81a6bd097b725620` and added its verified OpenSTA, DV, bounded proof, readback and admission evidence. [Rebase baseline checks](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/44050842183) [Milestone 12B #45](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/45)

The final handbook content and documentation checks are in `b0bc1c39bd7fa7607718d1244ca9e64f83071745`. The follow-up commit records the validation of that content.

## Native validation

Run `f3fb3ad771054bbca22ccad8cf24a72b` completed all seven pinned GCD stages in 153.37 s of stage time on WSL2 Ubuntu with Python 3.12.3, Docker 29.1.3, two tool threads and a 1,200 MiB container cap. A fresh virtual environment installed the editable package with `visuals,mcp`. The cached image verified as `linux/amd64`.

| Check | Observed result |
| --- | --- |
| Doctor and plan | Required checks passed. The planned output inventory covered all seven stages. |
| GCD physical verification | Profiled verification exited 1. Setup −1.20488 ns, TNS −54.4492 ns, 68 setup violations. Hold +0.429847 ns. Coverage, router, platform DRC, LVS and independent GDS reopening passed. |
| Qualification | Level 3, implementation passed and physical acceptance failed. |
| Readback and recovery | Engineering and physical reports, path diagnosis and resource history returned. Resume reused all seven identical implementation attempts. The report also reflected the verification performed before resume. |
| Images and Studio | KLayout layout and gallery rehash passed. Seven stage/GDS assets exported. The real Edge 157 smoke test checked the GCD workspace, timing pins, selection, comparison, measurement, verification, execution and canvas PNG provenance. |
| Native RTL | Yosys inspected the counter's 8-bit output. Icarus and SAT passed the XOR fixture and failed the isolated OR edit. Native resume passed after restoring the source. |
| Digest-bound renderer correction | The retained OpenROAD teardown log classified as `OPENROAD_PROCESS_SIGNAL`. The approved isolated floorplan replay returned `VERIFIED` with bound PNG and geometry hashes. |
| Agent and benchmark | SDK stdio returned GCD timing and schemas. Model-free observation, structured diagnosis, replay and keyword retrieval succeeded. Local Qwen2.5-0.5B via llama.cpp returned a validated explanation. The bounded optimization plan and single-case benchmark build and figure rebuild succeeded. |

The new GCD receipts, failures and captures remain in the local `.local/readme-handbook-validation-01/` directory of the source workspace. The committed handbook cites the historical published images and receipts by their original run identity. [Captured reference GCD result](evidence/gcd-walkthrough/receipt.json)

## Figures and checks

The handbook embeds 25 figures. Its one new asset renders four selected lines from the retained `1_synth.json` with Pillow ImageDraw. The original native metrics bytes are published alongside the image. Their digest matches the synthesis artifact hash in the GCD walkthrough. The figure shows an unplaced mapped design inventory. The other 24 figures are existing OpenROAD and KLayout captures, actual Studio browser captures, and charts generated from native evidence. Each caption links its provenance and describes the observed status. [Synthesis provenance](images/readme/provenance.json) · [Physical checkpoints](images/gcd-stage-snapshots/provenance.json) · [Studio captures](images/studio/provenance.json)

Documentation tests parse the README examples through the real `argparse` tree, compare flag scopes, validate checked-in example paths and inspect headings, anchor links, tables, fences and captions. Image SHA-256 values resolve through the linked receipts. The existing publication checks continue to bind physical, acceptance and failure evidence.

The parser checks 83 standalone CLI examples and one inline example for each of the 19 command families. This includes the 12B `--diagnostic-budget`, `--multi-clock` and `--seed` examples, plus the documented controller budget, profile, coverage, deck and report scopes.

| Validation on the rebased worktree | Result |
| --- | --- |
| `python -m pytest -q tests/test_documentation.py tests/test_visual_artifacts.py` | 15 passed |
| Full pytest suite with optional visualization and MCP dependencies | Windows Python 3.12.5, 288 passed and 16 optional skips. WSL2 Python 3.12.3, 287 passed and 17 optional skips. |
| Ruff lint and format, strict mypy, dependency check | Passed. Ruff format checked 171 files. Mypy checked 119 source files. |
| Native CLI readback on the rebased checkout | GCD plan byte-identical to the fresh native run plan. The bound 12B receipt returned Level 2, failed implementation, and blocked physical and design qualification. |
| GitHub Markdown and browser rendering | GitHub `gfm` Markdown rendered 19 major sections, 25 available figures and 16 tables. Mermaid 11.12.0 parsed and rendered all four diagrams. Headless Edge 157 verified desktop at 1,440 px and mobile at 390 px with no page-level horizontal overflow. Wide tables retain local scrolling. |

## Scope and remaining work

The handbook documents verified Milestone 12 and 12B evidence. The OpenTitan DV and physical attempts, the historical ICeWall and Ibex examples, and the full optimization campaign use previously qualified receipts. The new smoke checks exercise GCD, scoped local AI, the native RTL fixture, optimization planning and benchmark readback.

The documentation PR follows the separately merged 12B PR. The core GCD example was executed on the kickoff baseline. Post-rebase validation checks the current parser, all documentation and the unchanged GCD plan. The final integrated README and hosted checks are recorded against the delivery PR.
