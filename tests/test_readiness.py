import json
from pathlib import Path

import pytest

from autonomous_eda.macro_readiness import load_macro, logical_pins
from autonomous_eda.manifest import sha256
from autonomous_eda.partition_readiness import plan_partitions


def test_partition_inventory_retains_native_clock_and_reset_bindings(tmp_path: Path) -> None:
    topgen = tmp_path / "topgen.json"
    topgen.write_text(
        json.dumps(
            {
                "name": "demo",
                "module": [
                    {
                        "name": "ram",
                        "type": "sram_ctrl",
                        "clock_srcs": {"clk_i": "main"},
                        "reset_connections": {"rst_ni": "system"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    edam = tmp_path / "edam.json"
    edam.write_text(
        json.dumps(
            {
                "toplevel": "chip",
                "version": "0.2.1",
                "files": [
                    {"name": "../src/rtl/autogen/chip.sv", "file_type": "systemVerilogSource"},
                    {"name": "aux.cc", "file_type": "cppSource"},
                ],
            }
        ),
        encoding="utf-8",
    )
    report = plan_partitions(topgen, edam, "a" * 40)
    assert report["readiness_level"] == 0
    assert report["partitions"] == {
        "ram": {
            "block": "sram_ctrl",
            "clocks": {"clk_i": "main"},
            "resets": {"rst_ni": "system"},
            "base_addrs": {},
            "memory": {},
            "power_domains": [],
            "physical_boundary": "BLOCKED",
            "acceptance": "BLOCKED",
        }
    }
    assert report["native_manifest_file_types"] == {"cppSource": 1, "systemVerilogSource": 1}


def test_macro_manifest_declares_policy_and_yosys_ports() -> None:
    manifest = load_macro(Path("docs/evidence/earlgrey/dffram.toml"))
    assert manifest.implementation == "hard_macro"
    assert manifest.target_corner == "tt_025C_1v80"
    assert logical_pins(
        {
            "ports": {
                "CLK": {"direction": "input", "bits": [2]},
                "D": {"direction": "output", "bits": [4, 5], "offset": 5},
            }
        }
    ) == {"CLK": "INPUT", "D[5]": "OUTPUT", "D[6]": "OUTPUT"}


def test_macro_manifest_rejects_path_escape(tmp_path: Path) -> None:
    example = Path("docs/evidence/earlgrey/dffram.toml").read_text(encoding="utf-8")
    path = tmp_path / "bad.toml"
    path.write_text(
        example.replace('"DFFRAM_4K.lef"', '"../DFFRAM_4K.lef"'),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid lef"):
        load_macro(path)


def test_published_macro_inventory_preserves_missing_corner_blocker() -> None:
    folder = Path("docs/evidence/earlgrey")
    report = json.loads((folder / "dffram-readiness.json").read_bytes())
    assert report["manifest_sha256"] == sha256(folder / "dffram.toml")
    assert len(report["views"]["verilog"]["pins"]) == 80
    assert all(report["checks"].values())
    assert report["views"]["liberty"]["status"] == "MISSING"
    assert report["qualification"] == "BLOCKED"
