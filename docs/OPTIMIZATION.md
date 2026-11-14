# Budgeted physical-design optimization

The optimizer proposes and executes isolated changes through the existing seven-stage ORFS controller. It reuses valid checkpoints after interruption, runs the independent DRC and LVS targets, and evaluates the resulting evidence. The physical profile requires setup and hold to pass at the original clock. Full acceptance includes the additional schema 1 gates.

Install the base package on Linux or WSL2 for execution. The pinned image must already be cached. Use the [reference-index preparation steps](TIMING_DIAGNOSIS.md) to create a local GCD baseline copy. Keep its original evidence separate from the campaign directory.

~~~sh
autonomous-eda optimize plan experiments/gcd-search \
  --baseline .local/diagnosis-reference/gcd --utilization 30 46 24 34 42 52 \
  --strategy paired --max-candidates 4 --seconds 3600 --candidate-seconds 900 \
  --memory-mib 1200 --model qwen2.5-0.5b \
  --endpoint http://127.0.0.1:18117/v1/chat/completions
autonomous-eda optimize approve experiments/gcd-search --digest PLAN_DIGEST
autonomous-eda optimize run experiments/gcd-search
autonomous-eda optimize report experiments/gcd-search --json
autonomous-eda optimize frontier experiments/gcd-search --profile physical
autonomous-eda artifacts render experiments/gcd-search --all
autonomous-eda artifacts gallery experiments/gcd-search
~~~

The plan lists candidate settings and their digest before approval. Review the design, objectives, verification profile and resource limits. `status`, `compare` and `frontier` read the campaign database. `resume` continues the existing reservation. `cancel` records revocation and stops active execution at the next budget poll. A stopped campaign exports `report.json` and `report.md`. Rendering adds the PNGs and their manifests. The campaign database records Linux canonical paths, so inspect a WSL-created campaign from the same WSL mount.

With the `visuals` extra installed, `optimize run` and `resume` export campaign and run PNGs after execution. Use `--no-render` to defer that work. For standalone controller runs, use `verify RUN --render` after implementation or run the artifact renderer separately.

`grid` visits the supplied values in order. `random` shuffles that same finite set with `--seed`. `guided` asks a local model to choose among remaining legal values. `paired` runs the first half as a grid and uses guided selection for the remaining reservations. A disabled or rejected model response uses the next legal grid value and records the fallback. Proposal effects and resource estimates remain predictions. The evaluator reads measured outcomes from the controller and verification artifacts.

Ibex uses its own 8 ns constraints and baseline utilization of 50%. It supports planning, validation and retained-evidence comparison. Execution approval is limited to GCD in this release. See the [policy](OPTIMIZATION_POLICY.md), [schema](OPTIMIZATION_SCHEMA.md), [evaluation](OPTIMIZATION_EVALUATION.md) and [example gallery](EXAMPLE_GALLERY.md).
