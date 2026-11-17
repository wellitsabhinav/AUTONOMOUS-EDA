import json
from pathlib import Path
from typing import Any, cast

import pytest

from autonomous_eda.aes_contract import (
    PORT_ROLES,
    block_clock,
    diagnostic_constraints,
    integration_connections,
    require_digest,
)
from autonomous_eda.aes_qualification import GATES, RECEIPT, digest_payload, read
from autonomous_eda.evidence import Experiment
from autonomous_eda.manifest import fingerprint, sha256
from autonomous_eda.opentitan_physical import SDC
from autonomous_eda.verification.aes_formal import proof_status
from autonomous_eda.verification.aes_native import (
    CORE_MARKER,
    differential,
    interface_ports,
    regression,
    simulation_result,
)
from autonomous_eda.verification.aes_scenarios import (
    CASES,
    DEFAULTS,
    PROFILES,
    SIDELOAD,
    SOFTWARE,
    first_difference,
    scenario_observations,
)
from autonomous_eda.verification.process import Execution

CLOCKS = {name: {"direction": "input", "width": 1} for name in ("clk_i", "clk_edn_i")}


def test_clock_inventory_is_literal_and_reproducible() -> None:
    result = block_clock(SDC, CLOCKS)
    assert result["period_ns"] == 8.5
    assert result["ports"] == ["clk_i", "clk_edn_i"]
    assert fingerprint(result) == fingerprint(
        block_clock(SDC, dict(reversed(list(CLOCKS.items()))))
    )


@pytest.mark.parametrize(
    ("sdc", "message"),
    [
        (SDC.replace("clk_i clk_edn_i", "clk_i"), "both AES clock inputs"),
        (SDC.replace("clk_edn_i", "no_clock"), "unknown or invalid"),
        (SDC.replace("8.5", "9.0"), "differs from"),
        (SDC + "set_false_path -from [all_inputs]\n", "unsupported"),
        (SDC + "create_generated_clock -source clk_i out\n", "unsupported"),
    ],
)
def test_unsupported_clock_assumptions(sdc: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        block_clock(sdc, CLOCKS)


def test_modified_input_and_receipt_fail_integrity(tmp_path: Path) -> None:
    source = tmp_path / "input"
    source.write_bytes(b"original")
    digest = sha256(source)
    require_digest(source, digest, "test input")
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="modified"):
        require_digest(source, digest, "test input")
    payload: dict[str, Any] = {"schema_version": 1, "status": "BLOCKED"}
    report = {**payload, "receipt": fingerprint(payload)}
    digest_payload(json.loads(json.dumps(report)), "receipt")
    with pytest.raises(ValueError, match="integrity"):
        digest_payload({**report, "status": "PASS"}, "receipt")


def log_run(work: Path, out: str, *, code: int = 0, stop: str | None = None) -> Execution:
    (work / "out.log").write_text(out, encoding="utf-8")
    (work / "err.log").write_text("", encoding="utf-8")
    return Execution(["test"], code, "", 0.2, stop, "out.log", "err.log")


def test_parses_native_core_summary(tmp_path: Path) -> None:
    run = log_run(tmp_path, (CORE_MARKER + "\n") * 2 + "Executed cycles:  3297\n")
    assert simulation_result(tmp_path, run, "core") == (
        "PASS",
        "Upstream cipher-core oracle completed both round trips",
        {"cycles": [3297], "success_markers": 2},
    )


@pytest.mark.parametrize(
    ("log", "code", "stop", "status"),
    [
        ("AEDA_AES_FAIL AES word differs\n", 1, None, "FAIL"),
        ("AEDA_AES_PASS\n", 0, None, "INFRASTRUCTURE_ERROR"),
        ("AEDA_AES_TRACE 2 00\nAEDA_AES_PASS\n", 0, None, "INFRASTRUCTURE_ERROR"),
        ("AEDA_AES_TRACE 0 00\nAEDA_AES_PASS\n", 1, None, "INFRASTRUCTURE_ERROR"),
        ("AEDA_AES_TRACE 0 00\nAEDA_AES_PASS\n", 0, "timeout", "TIMEOUT"),
        ("AEDA_AES_TRACE 0 00\nAEDA_AES_PASS\n", 0, "cancelled", "BLOCKED"),
    ],
)
def test_peripheral_oracle_and_dispatch_outcomes(
    tmp_path: Path, log: str, code: int, stop: str | None, status: str
) -> None:
    assert (
        simulation_result(tmp_path, log_run(tmp_path, log, code=code, stop=stop), "peripheral")[0]
        == status
    )


def test_replayed_differential_detects_one_changed_cycle(tmp_path: Path) -> None:
    def replay(value: str) -> dict[str, Any]:
        run = log_run(tmp_path, f"AEDA_AES_TRACE 0 00\nAEDA_AES_TRACE 1 {value}\nAEDA_AES_PASS\n")
        status, _, facts = simulation_result(tmp_path, run, "peripheral")
        return {"status": status, "observations": facts}

    expected = replay("ca")
    assert replay("ca") == expected
    assert differential(expected, expected)["status"] == "PASS"
    assert differential(expected, replay("cb"))["status"] == "FAIL"
    assert differential(expected, {"status": "TIMEOUT"})["status"] == "BLOCKED"


def test_missing_native_simulator_remains_blocked(tmp_path: Path) -> None:
    result = regression(
        tmp_path,
        scope="core",
        sources={},
        build_args=[],
        simulation="missing-product",
        tool_name="aeda-test-unavailable-tool",
        timeout=1,
    )
    assert result["status"] == "BLOCKED"
    assert result["execution"] == []


def test_compile_failure_never_reaches_functional_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = tmp_path / "verilator"
    tool.write_text("tool fixture", encoding="utf-8")
    monkeypatch.setattr("autonomous_eda.verification.aes_native.shutil.which", lambda _: str(tool))
    calls = []

    def launch(
        command: list[str], work: Path, label: str, deadline: float, cancelled: Any
    ) -> Execution:
        calls.append(label)
        if label == "version":
            return log_run(work, "Verilator fixture\n")
        return log_run(work, "AEDA_AES_FAIL compilation report\n", code=1)

    monkeypatch.setattr("autonomous_eda.verification.aes_native.execute", launch)
    result = regression(
        tmp_path,
        scope="peripheral",
        sources={},
        build_args=["{verilator}"],
        simulation="missing-product",
        timeout=1,
    )
    assert calls == ["version", "compile"]
    assert result["status"] == "INFRASTRUCTURE_ERROR"


def test_generated_clock_nets_must_agree_with_topgen() -> None:
    bindings = {name: "main_aes" if name.startswith("clk_") else name for name in PORT_ROLES}
    topgen = {"clk_i": "main", "clk_edn_i": "main"}
    instantiation = ") u_aes (\n" + "\n".join(f".{k}({v})," for k, v in bindings.items())
    assert integration_connections(instantiation + "\n  );", topgen) == bindings
    with pytest.raises(ValueError, match="Conflicting"):
        integration_connections(
            instantiation.replace("clk_edn_i(main_aes)", "clk_edn_i(other)"), topgen
        )


@pytest.mark.parametrize(
    "budget",
    [
        {"inputs": {"tl_i": {"max": 1}}},
        {"inputs": {"tl_i": {"min": 2, "max": 1}}},
        {"outputs": {"edn_i": {"min": 0, "max": 1}}},
        {"uncertainty_ns": {"hold": 0}},
        {"exceptions": ["set_false_path -from [all_inputs]"]},
        {"inputs": {"tl_i": {"min": False, "max": 1}}},
        {"inputs": {"tl_i": {"min": 0, "max": float("inf")}}},
    ],
)
def test_diagnostic_constraints_require_typed_min_max_and_valid_objects(
    tmp_path: Path, budget: dict[str, Any]
) -> None:
    path = tmp_path / "budget.json"
    path.write_text(json.dumps(budget), encoding="utf-8")
    with pytest.raises(ValueError):
        diagnostic_constraints(path)


def test_diagnostic_constraints_remain_experimental(tmp_path: Path) -> None:
    path = tmp_path / "budget.json"
    path.write_text('{"inputs":{"edn_i":{"min":0,"max":1}}}', encoding="utf-8")
    fact, commands = diagnostic_constraints(path)
    assert fact["classification"] == "EXPERIMENTAL"
    assert fact["sha256"] == sha256(path)
    assert commands == [
        "set_input_delay -clock main -min 0.0 $aeda_ports(edn_i)",
        "set_input_delay -clock main -max 1.0 $aeda_ports(edn_i)",
    ]


def scenario_fixture(name: str) -> tuple[dict[str, int], list[str]]:
    config = {**DEFAULTS, **PROFILES[name]}
    vectors: tuple[tuple[str, str], ...] = SOFTWARE
    if name == "sideload":
        vectors += SIDELOAD
    elif name == "errors":
        vectors += (SOFTWARE[1],)
    lines = [
        "AEDA_AES_TRACE 0 01",
        "AEDA_AES_EDN 0 01",
        *(f"AEDA_AES_VECTOR {label} {value} {value}" for label, value in vectors),
        *(
            f"AEDA_AES_CASE {case}"
            for case in ("idle_reset", "illegal_register", *CASES.get(name, []))
        ),
        f"AEDA_AES_COUNTS 10 1 {config['edn_delay']}",
    ]
    for domain, prefix in (("CORE", "clk"), ("EDN", "edn")):
        half, phase = config[f"{prefix}_half"], config[f"{prefix}_phase"]
        lines.extend(
            (
                f"AEDA_AES_EDGE {domain} 0 {phase + half}",
                f"AEDA_AES_EDGE {domain} 1 {phase + 3 * half}",
            )
        )
    return config, lines


@pytest.mark.parametrize("name", PROFILES)
def test_scenario_oracle_requires_known_answers_and_coverage(name: str) -> None:
    config, lines = scenario_fixture(name)
    assert scenario_observations(lines, name, config)[0] == "PASS"
    bad_vector = lines.copy()
    bad_vector[2] = bad_vector[2][:-32] + 32 * "0"
    assert scenario_observations(bad_vector, name, config)[0] == "FAIL"
    for case in ("illegal_register", *CASES.get(name, [])):
        short = [line for line in lines if line != f"AEDA_AES_CASE {case}"]
        assert scenario_observations(short, name, config)[0] == "INFRASTRUCTURE_ERROR"


def test_independent_clock_and_edn_evidence_are_measured() -> None:
    config, lines = scenario_fixture("independent")
    assert scenario_observations(lines, "independent", config)[2]["first_edges_ns"] == {
        "CORE": [(0, 5), (1, 13)],
        "EDN": [(0, 9), (1, 23)],
    }
    same_clock = [line.replace("EDN 1 23", "EDN 1 13") for line in lines]
    assert scenario_observations(same_clock, "independent", config)[0] == "FAIL"
    no_wait = [line.replace("COUNTS 10 1 9", "COUNTS 10 1 0") for line in lines]
    assert scenario_observations(no_wait, "independent", config)[0] == "FAIL"


def test_differential_failure_has_first_edge_and_both_values(tmp_path: Path) -> None:
    original, translated = tmp_path / "original.log", tmp_path / "translated.log"
    original.write_text("AEDA_AES_EDN 0 01\nAEDA_AES_EDN 1 02\n", encoding="utf-8")
    translated.write_text("AEDA_AES_EDN 0 01\nAEDA_AES_EDN 1 b2\n", encoding="utf-8")
    assert first_difference(original, translated) == {
        "domain": "EDN",
        "edge": 1,
        "original": (1, "02"),
        "translated": (1, "b2"),
    }


def test_formal_timeout_cannot_publish_a_partial_success(tmp_path: Path) -> None:
    marker = "SAT proof finished - no model found: SUCCESS!"
    assert proof_status(log_run(tmp_path, marker, stop="timeout"), marker) == "TIMEOUT"
    assert proof_status(log_run(tmp_path, marker, code=1), marker) == "INFRASTRUCTURE_ERROR"


def test_historical_receipt_readback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = Path("docs/evidence/opentitan-aes/qualification/aes-qualification.json")
    receipt = json.loads(archive.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        "autonomous_eda.aes_qualification.identity",
        lambda _: {key: receipt[key] for key in ("run_id", "rtl_revision", "translation_sha256")},
    )
    output = tmp_path / RECEIPT
    output.parent.mkdir()
    output.write_bytes(archive.read_bytes())
    evidence = cast(Experiment, {})
    assert read(tmp_path, evidence) == receipt
    assert set(receipt["gates"]) == set(GATES)
    receipt["gates"]["formal"] = "PASS"
    output.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        read(tmp_path, evidence)


def test_native_widths_and_directions() -> None:
    assert interface_ports("    VL_IN8(&clk_i,0,0);\n    VL_OUTW(&tl_o,65,0,3);") == {
        "clk_i": {"direction": "input", "width": 1},
        "tl_o": {"direction": "output", "width": 66},
    }
