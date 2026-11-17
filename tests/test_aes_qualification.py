import json
from pathlib import Path
from typing import Any

import pytest

from autonomous_eda.aes_contract import block_clock, require_digest
from autonomous_eda.aes_qualification import digest_payload
from autonomous_eda.manifest import fingerprint, sha256
from autonomous_eda.opentitan_physical import SDC
from autonomous_eda.verification.aes_native import (
    CORE_MARKER,
    differential,
    interface_ports,
    regression,
    simulation_result,
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


def test_native_widths_and_directions() -> None:
    assert interface_ports("    VL_IN8(&clk_i,0,0);\n    VL_OUTW(&tl_o,65,0,3);") == {
        "clk_i": {"direction": "input", "width": 1},
        "tl_o": {"direction": "output", "width": 66},
    }
