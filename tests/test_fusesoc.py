import json
from pathlib import Path

import pytest

from autonomous_eda.fusesoc import import_edam
from autonomous_eda.manifest import RTLManifest, load_manifest, sha256
from autonomous_eda.rtl_inventory import summarize

REVISION = "a" * 40


@pytest.fixture
def exported(tmp_path: Path) -> Path:
    ordered = [
        ("rtl/p.sv", "vendor:lib:pkg:0", False),
        ("include/p.svh", "vendor:lib:pkg:0", True),
        ("generated/wrapper.sv", "vendor:generated:wrapper:0", False),
        ("rtl/top.sv", "vendor:ip:top:0", False),
    ]
    files = []
    for name, core, include in ordered:
        source = tmp_path / "src" / name
        source.parent.mkdir(exist_ok=True, parents=True)
        source.write_text(f"// {name}\n", encoding="utf-8")
        files.append(
            {
                "file_type": "systemVerilogSource",
                "name": f"../src/{name}",
                "core": core,
                "is_include_file": include,
            }
        )
    edam = tmp_path / "syn-icarus" / "top.eda.json"
    edam.parent.mkdir()
    edam.write_text(
        json.dumps(
            {
                "version": "0.2.1",
                "toplevel": "top",
                "files": files,
                "dependencies": {"vendor:ip:top:0": ["vendor:lib:pkg:0"]},
                "parameters": {
                    "SYNTHESIS": {"paramtype": "vlogdefine", "default": True},
                    "DEBUG": {"paramtype": "vlogdefine", "default": False},
                },
            }
        ),
        encoding="utf-8",
    )
    return edam


def test_import_preserves_native_order_and_input_identity(exported: Path, tmp_path: Path) -> None:
    result = import_edam(exported, tmp_path / "design.toml", REVISION, ("vendor:generated:",))
    manifest = load_manifest(tmp_path / "design.toml")
    assert isinstance(manifest, RTLManifest)
    assert result["ordered_sources"] == [
        "src/rtl/p.sv",
        "src/generated/wrapper.sv",
        "src/rtl/top.sv",
    ]
    assert manifest.frontend == "sv2v"
    assert manifest.defines == (("SYNTHESIS", "1"),)
    assert result["edam_sha256"] == sha256(exported)
    assert result["manifest_sha256"] == manifest.fingerprint()
    generated = tmp_path / "src/generated/wrapper.sv"
    assert result["generated_sources_sha256"] == {"src/generated/wrapper.sv": sha256(generated)}
    (tmp_path / "src/include/p.svh").write_text("// changed", encoding="utf-8")
    assert result["manifest_sha256"] != manifest.fingerprint()


@pytest.mark.parametrize(
    "filename, diagnostic",
    [
        ("../../elsewhere/other.sv", "under ../src/"),
        ("../src/../other.sv", "invalid EDAM file"),
        ("../src/rtl/missing.sv", "missing EDAM file"),
    ],
)
def test_import_fails_closed_on_invalid_files(
    exported: Path, tmp_path: Path, filename: str, diagnostic: str
) -> None:
    data = json.loads(exported.read_text(encoding="utf-8"))
    data["files"][0]["name"] = filename
    exported.write_text(json.dumps(data), encoding="utf-8")
    output = tmp_path / "design.toml"
    with pytest.raises(ValueError, match=diagnostic):
        import_edam(exported, output, REVISION)
    assert not output.exists()


def test_inventory_counts_instances_and_observes_register_controls() -> None:
    inventory = summarize(
        "top",
        {
            "top": {"cells": {"first": {"type": "child"}, "second": {"type": "child"}}},
            "child": {
                "cells": {
                    "reg": {
                        "type": "$adff",
                        "connections": {"CLK": [2], "ARST": [3]},
                        "parameters": {"CLK_POLARITY": "1", "ARST_POLARITY": "0"},
                    },
                    "macro": {"type": "sram"},
                },
                "netnames": {"core_clock": {"bits": [2]}, "reset_n": {"bits": [3]}},
                "memories": {"scratch": {"width": 8, "size": 16}},
            },
            "sram": {"attributes": {"blackbox": "1"}},
            "unused": {},
        },
        elaborated=True,
    )
    assert inventory["module_definition_count"] == 3
    assert inventory["hierarchy_instance_count"] == 4
    assert inventory["blackbox_module_instances"] == {"sram": 2}
    assert inventory["sequential_cells"] == {"$adff": 2}
    assert inventory["primitive_cell_count"] == 2
    assert inventory["inferred_memories"] == {
        "child": {"module_instances": 2, "memories": {"scratch": {"width": 8, "depth": 16}}}
    }
    assert inventory["clock_and_reset_controls"] == {
        "child": [
            {"pin": "ARST", "bits": [3], "net": "reset_n", "polarity": 0, "cells_in_definition": 1},
            {
                "pin": "CLK",
                "bits": [2],
                "net": "core_clock",
                "polarity": 1,
                "cells_in_definition": 1,
            },
        ]
    }
