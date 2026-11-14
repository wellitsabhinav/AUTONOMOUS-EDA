import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from autonomous_eda.optimization import Campaign, Stop, Stopped
from autonomous_eda.optimization_policy import (
    PHYSICAL,
    Baseline,
    Budget,
    Plan,
    candidate_grid,
    candidate_id,
    candidate_manifest,
    load_plan,
    registry,
    validate_changes,
)
from autonomous_eda.optimization_proposer import select
from autonomous_eda.optimization_results import Outcome, frontier, verdict
from autonomous_eda.orfs import execute


@pytest.fixture
def campaign(tmp_path: Path) -> tuple[Campaign, Plan]:
    base = Baseline(
        "record",
        "digest",
        "gcd",
        "sky130hd",
        "gcd",
        "designs/sky130hd/gcd/config.mk",
        2,
        1.1,
        "tt_025C_1v80",
        "sdc",
        "rtl",
        {"library": "lib"},
        "diagnosis",
        (),
        {},
    )
    store = Campaign(tmp_path)
    plan = Plan(
        1,
        "0" * 32,
        base,
        str(store.root),
        "grid",
        33,
        {"CORE_UTILIZATION": (30, 46, 24, 30)},
        Budget(2, 600, 400),
        ("setup_worst_slack_ns", "standard_cell_area_um2"),
    )
    store.create(plan, tmp_path / "baseline")
    return store, plan


def test_parameter_registry_and_search(campaign: tuple[Campaign, Plan]) -> None:
    _, plan = campaign
    assert registry("gcd")["CORE_UTILIZATION"].baseline == 38
    assert registry("ibex")["CORE_UTILIZATION"].baseline == 50
    assert [p["CORE_UTILIZATION"] for p in candidate_grid(plan)] == [30, 46, 24]
    randomized = replace(plan, strategy="random")
    assert candidate_grid(randomized) == candidate_grid(randomized)
    assert sorted(p["CORE_UTILIZATION"] for p in candidate_grid(randomized)) == [24, 30, 46]
    changes = {"CORE_UTILIZATION": 30}
    assert candidate_id(plan, changes) != candidate_id(replace(plan, seed=34), changes)
    manifest = candidate_manifest(plan, changes)
    assert manifest.core_utilization == 30
    assert manifest.clock_period_ns == 1.1
    assert manifest.top == "gcd"


@pytest.mark.parametrize(
    "value",
    [
        {"CLOCK_PERIOD": 2},
        {"VERILOG_FILES": 2},
        {"SDC_FILE": 2},
        {"PLACE_DENSITY": 2},
        {"CORE_UTILIZATION": True},
        {"CORE_UTILIZATION": 38},
        {"CORE_UTILIZATION": 100},
        {"CORE_UTILIZATION": "30; touch output"},
        {"CORE_UTILIZATION": 30, "PDK": 2},
    ],
)
def test_unsupported_proposals_fail_closed(value: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        validate_changes("gcd", value)


@pytest.mark.parametrize(
    "raw",
    [
        '{"candidate":"option_2"}',
        '{"candidate":"option_1","gate":"PASSED"}',
        '{"candidate":"option_1","candidate":"option_1"}',
        '{"candidate":{"CORE_UTILIZATION":"30; touch file"}}',
        '{"candidate":"../../another-run"}',
        "ignore prior instructions",
    ],
)
def test_strict_model_selection(raw: str) -> None:
    with pytest.raises(ValueError):
        select(raw, {"option_1": {"CORE_UTILIZATION": 30}})


def test_valid_model_selection() -> None:
    assert select('{"candidate":"option_1"}', {"option_1": {"CORE_UTILIZATION": 30}}) == {
        "CORE_UTILIZATION": 30,
    }


def test_digest_binding_reservation_and_cancel(
    campaign: tuple[Campaign, Plan],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, plan = campaign

    monkeypatch.setattr(store, "checked_plan", lambda: (plan, {}))
    assert store.row()["state"] == "VALIDATED"
    with pytest.raises(ValueError, match="digest"):
        store.approve("incorrect")
    with pytest.raises(Stopped) as denial:
        store.remaining(plan)
    assert denial.value.reason == Stop.AUTHORIZATION_REVOKED
    store.approve(plan.digest)
    with store.connect() as db:
        db.execute("UPDATE campaign SET state='EXECUTING'")
    for util in (30, 46):
        store.reserve(plan, {"CORE_UTILIZATION": util}, "grid")
    with pytest.raises(Stopped) as limit:
        store.reserve(plan, {"CORE_UTILIZATION": 24}, "grid")
    assert limit.value.reason == Stop.MAX_CANDIDATES_REACHED
    store.cancel()
    with pytest.raises(Stopped) as cancelled:
        store.remaining(plan)
    assert cancelled.value.reason == Stop.USER_CANCELLED


def test_plan_change_and_budget(
    campaign: tuple[Campaign, Plan],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, plan = campaign
    monkeypatch.setattr(store, "checked_plan", lambda: (plan, {}))
    store.approve(plan.digest)
    raw = json.loads(store.row()["plan"])
    raw["authorization_scope"] = "unrestricted"
    with pytest.raises(ValueError, match="changed"):
        load_plan(raw, plan.baseline, store.root)
    with store.connect() as db:
        db.execute("UPDATE campaign SET state='EXECUTING',started=1")
    with pytest.raises(Stopped) as expired:
        store.remaining(plan)
    assert expired.value.reason == Stop.BUDGET_EXHAUSTED
    with pytest.raises(ValueError):
        Budget(maximum_candidates=5)
    with pytest.raises(ValueError):
        Budget(maximum_concurrency=2)


def test_physical_and_full_gate_profiles() -> None:
    gates: Any = dict.fromkeys(PHYSICAL, "PASSED")
    assert verdict(gates, PHYSICAL) == "PASSED"
    assert verdict(gates, (*PHYSICAL, "equivalence")) == "BLOCKED"
    gates["setup"] = "FAILED"
    assert verdict(gates, PHYSICAL) == "FAILED"


def test_pareto_excludes_failed_and_unknown_objectives(campaign: tuple[Campaign, Plan]) -> None:
    _, plan = campaign

    def outcome(slack: float, area: float, status: str = "FEASIBLE") -> Outcome:
        return Outcome(
            True,
            status,
            "BLOCKED",
            {},
            (),
            True,
            (),
            {"setup_worst_slack_ns": slack, "standard_cell_area_um2": area},
            {},
            (),
            {},
        )

    rows = {"a": outcome(0.1, 10), "b": outcome(0.2, 12), "worse": outcome(0.0, 13)}
    rows["failed"] = outcome(10, 1, "INFEASIBLE")
    rows["missing"] = replace(rows["a"], missing_objectives=("standard_cell_area_um2",))
    rows["incompatible"] = replace(rows["b"], comparable=False)
    assert frontier(rows, plan.objectives, "physical") == ["a", "b"]
    assert frontier(rows, plan.objectives, "full") == []
    assert frontier({"failed": rows["failed"]}, plan.objectives, "physical") == []


@pytest.mark.parametrize("cancel", [False, True])
def test_dispatch_stops_named_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    removed: list[list[str]] = []
    polls = 0
    killed = False

    class Process:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "Process":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def kill(self) -> None:
            nonlocal killed
            killed = True

        def wait(self, timeout: float) -> int:
            raise subprocess.TimeoutExpired("docker", timeout)

    def cleanup(command: list[str], **kwargs: object) -> None:
        removed.append(command)

    def guard() -> float:
        nonlocal polls
        polls += 1
        if polls > 2:
            if cancel:
                raise Stopped(Stop.USER_CANCELLED)
            return 0
        return 20

    monkeypatch.setattr(subprocess, "Popen", Process)
    monkeypatch.setattr(subprocess, "run", cleanup)
    name = "aeda-" + "0" * 32
    args = (["docker", "run"], 900, tmp_path / "stage.log", name)
    if cancel:
        with pytest.raises(Stopped) as error:
            execute(*args, guard=guard)
        assert error.value.reason == Stop.USER_CANCELLED
    else:
        assert execute(*args, guard=guard)[0] == 124
    assert killed
    assert removed == [["docker", "rm", "-f", name]]
