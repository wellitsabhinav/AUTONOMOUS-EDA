# ORFS resource telemetry

Controller-managed ORFS stages record one resource journal per Docker invocation.
The seven implementation stages, independent GDS reopening, extracted timing coverage,
and platform DRC and LVS
use this lifecycle. Each attempt keeps its combined native stdout and stderr log,
`<stage>.resources.ndjson` samples, and a `<stage>.resources.json` terminal receipt.
The controller SHA-256 hashes these files into the attempt record. Existing schema 0
and schema 2 run databases remain readable. Retained runs without samples have unknown
container resource use.

## Sources and units

| Observation | Source and unit |
| --- | --- |
| CPU, RAM, swap and process allocation | Controller policy and Docker HostConfig. CPU allocation is in cores, Docker NanoCPUs in billionths of a core, RAM and swap in bytes and limits in processes. Docker MemorySwap includes RAM. |
| Memory use and highest observed kernel high-water counter | Linux cgroup v2 `memory.current` and `memory.peak`, or corresponding v1 memory controller files, in bytes |
| CPU time and throttling | Linux cgroup CPU counters, normalized to microseconds and event counts |
| OOM and process-limit evidence | Fresh per-invocation cgroup counters, Docker `State.OOMKilled`, and bounded Docker OOM event lookup on ambiguous failed exits |
| Sampled process RSS | Sum of readable process `VmRSS` values in bytes. Shared pages can appear in multiple processes. |
| Host available memory | Linux `/proc/meminfo` `MemAvailable`, converted to bytes |
| Experiment, temporary, and retained log usage | Bounded directory scan and file sizes in bytes, with filesystem capacity and free bytes from statvfs |
| Duration and disposition | Controller monotonic elapsed seconds, UTC timestamps, and a pre-cleanup Docker state snapshot |

A sample identifies its timestamp, cgroup source, Docker ID and each measurement's
provenance. Sampling targets a two-second interval and stops at 8,192 retained rows.
Elapsed timestamps show the actual spacing. The kernel high-water value describes the
container peak through the sample time. CPU counters, sampled RSS and storage values
can miss activity between the last sample and process exit. Missing observations are
absent from the measurement map and explained as unknown. A zero value denotes an
observed zero.

## Interruption and recovery

The controller creates a fresh named container with automatic removal disabled, then
starts it attached and journals samples. It records Docker terminal state before
explicit removal. A separate in-container watchdog bounds native execution if the
controller dies. The observer uses a daemon thread, bounded Docker queries and capped
directory walks. Cleanup proceeds independently of that thread.

SIGTERM and wall-time budget exhaustion trigger cleanup. A killed controller can leave
a stopped container until the next resume. Resume captures any surviving state and
complete sampled rows before removing the container and marks the interrupted attempt
separately. Output logs and valid upstream checkpoints remain available. Short stages,
unavailable host cgroups and unreadable process information can leave partial telemetry.

The dispatch classifications are `COMPLETED`, `WALL_TIMEOUT`,
`CPU_OR_PROCESS_LIMIT`, `CONFIRMED_OOM`, `SIGNAL_TERMINATED`, `TOOL_FAILURE`,
`RESOURCE_OBSERVATION_UNAVAILABLE` and `UNKNOWN_FAILURE`. Controller timer and
signal evidence are explicit. Exit 137 requires separate OOM evidence to establish
`CONFIRMED_OOM`. Exit 124 is a controller timeout only when the controller records
that it enforced a wall limit. Physical acceptance uses independent artifact and
verification evidence.

## Validation

```bash
PYTHONPATH=src AEDA_NATIVE_RESOURCE_TEST=1 python -m pytest -q tests/test_resource_telemetry.py
```

The optional tests use a cached pinned image, with a 48 MiB memory cap and zero
additional swap per disposable container. Tests exercise a bounded 96 MiB allocation,
an explicit exit 137, a stage timeout and SIGTERM/SIGKILL controller interruption.
The [milestone evaluation](MILESTONE_11_EVALUATION.md) records the measured host and
physical outcomes.

## Plan a bounded retry

`resources plan` verifies staged run inputs and the upstream checkpoint chain, then
reports available host RAM and CPU, filesystem headroom, the retained experiment size
and the target stage's recorded resource observations. A configurable margin reserves
headroom for the host and above measured stage use. The verdict is one of `READY`,
`RESOURCE_CONSTRAINED`, `INSUFFICIENT_RESOURCES` or `UNKNOWN`.

An incomplete route provides a runtime lower bound. A successful global route provides
no measured detailed-route memory requirement. A completion-size storage estimate is
also unavailable when the target stage never finished. The planner checks whether the
copy and margin fit the supplied retained artifact cap and whether the destination
filesystem can cover that cap.

`resources retry` requires an explicit execution policy. The `evidence-required` policy
permits a `READY` plan. The `bounded-trial` policy additionally permits a constrained
plan with finite limits and a verified elapsed observation. The retry rechecks live
limits, requires a new directory, copies the verified checkpoint history and assigns
a new run ID. The copied upstream stages must pass their hash checks on dispatch.
The inherited manifest and container limits remain pinned. The stage timeout override
and experiment-wide guard are recorded in `resource-plan.json`.
