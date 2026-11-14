"""Chip manifest, classification, cache and real tool regressions."""

from __future__ import annotations

import os
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from autonomous_eda.io_chip import ChipReport, assemble, verdict
from autonomous_eda.io_chip_config import load
from autonomous_eda.io_chip_db import checked_mapping
from autonomous_eda.io_chip_layout import gds_matches, reopened
from autonomous_eda.io_chip_run import stage_key, verified_stage
from autonomous_eda.io_ring import approve, load_config, plan, run
from autonomous_eda.manifest import sha256
from autonomous_eda.verification.report import atomic

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "io"
CHIP = EXAMPLES / "chip.toml"
PLACEMENT = EXAMPLES / "placement.toml"


@pytest.mark.parametrize(
    ("original", "changed", "error"),
    [
        ('pad = "pad_out"', 'pad = "pad_in"', "duplicate"),
        ('external_port = "pad_y"', 'external_port = "pad_a"', "duplicate"),
        ('external_port = "pad_y"', 'external_port = "a"', "collides with another core port"),
        ('core_pin = "OUT"', 'core_pin = "PAD"', "expected core-facing pin OUT"),
        ('pad = "pad_out"', 'pad = "pad_ground"', "exactly one mapping"),
        ('min_layer = "met1"', 'min_layer = "met3"', "contain the met2"),
        ('signoff = "required"', 'signoff = "off"', "explicitly excluded"),
        ('signoff = "required"', 'domain = "other"', "invalid chip configuration fields"),
    ],
)
def test_chip_mapping_and_policy(tmp_path: Path, original: str, changed: str, error: str) -> None:
    config = tmp_path / "chip.toml"
    config.write_text(CHIP.read_text().replace(original, changed), encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        checked_mapping(load(config), load_config(PLACEMENT))


def test_unmapped_signal_and_classification() -> None:
    config = load(CHIP)
    placement = load_config(PLACEMENT)
    assert checked_mapping(config, placement)[0][:4] == ("pad_in", "a", "INPUT", "IN")
    with pytest.raises(ValueError, match="each signal"):
        checked_mapping(replace(config, mappings=config.mappings[:1]), placement)
    report = ChipReport()
    report.set("power_connectivity", "FAIL", "mixed")
    report.set("sta", "SKIPPED", "policy")
    assert verdict(report.gates) == "BLOCKED"
    for gate in report.gates:
        report.set(gate, "PASS", "verified")
    assert verdict(report.gates) == "PASS"
    report.set("drc", "FAIL", "violation")
    assert verdict(report.gates) == "FAIL"


def test_gds_qualification_requires_unambiguous_master_and_reopen() -> None:
    assert gds_matches({"master_views": {"pad": 1}}, {"pad": 2})
    assert not gds_matches({"master_views": {"pad": 2}}, {"pad": 2})
    assert not gds_matches({"master_views": {}}, {"pad": 2})
    layout = {
        "top": "chip",
        "top_levels": ["chip"],
        "empty_references": [],
        "boundary": True,
        "top_geometry_shapes": 1,
        "hierarchical_cells": 2,
        "masters": {"pad": 2},
    }
    assert reopened(layout, "chip", {"pad": 2})
    assert not reopened({**layout, "empty_references": ["missing"]}, "chip", {"pad": 2})
    assert not reopened({**layout, "boundary": False}, "chip", {"pad": 2})
    assert not reopened({**layout, "masters": {"pad": 1}}, "chip", {"pad": 2})


def test_stage_key_is_dependency_scoped(tmp_path: Path) -> None:
    files = ("placement.def", "pad.lef", "pad.gds", "pad.lib", "mapping", "routing", "signoff")
    dependencies = {
        "abstracts": ("pad.lef",),
        "connect": ("placement.def", "mapping"),
        "route": ("routing", "mapping", "placement.def"),
        "gds": ("pad.gds", "placement.def", "mapping", "routing", "pad.lef"),
        "liberty": ("pad.lib",),
        "signoff": files,
    }
    for name in files:
        (tmp_path / name).write_text(name)

    def keys() -> dict[str, str]:
        return {
            stage: stage_key(stage, [], {p: sha256(tmp_path / p) for p in sources}, {}, {})
            for stage, sources in dependencies.items()
        }

    baseline = keys()
    for name in files:
        (tmp_path / name).write_text("changed")
        changed = keys()
        assert {stage for stage in changed if changed[stage] != baseline[stage]} == {
            stage for stage, sources in dependencies.items() if name in sources
        }
        (tmp_path / name).write_text(name)


def test_stage_with_modified_output_is_stale(tmp_path: Path) -> None:
    attempt = "a" * 32
    key = "b" * 64
    work = tmp_path / attempt
    work.mkdir()
    output = work / "connected.def"
    output.write_text("first")
    receipt = work / "receipt.json"
    atomic(
        receipt, {"key": key, "outputs": {output.name: sha256(output)}, "inputs": {}, "scripts": {}}
    )
    atomic(tmp_path / f"{key}.json", {"attempt": attempt, "receipt_sha256": sha256(receipt)})
    assert verified_stage(tmp_path, key).output(output.name) == output
    output.write_text("stale")
    with pytest.raises(ValueError, match="stale chip stage"):
        verified_stage(tmp_path, key)


@pytest.mark.skipif(not os.environ.get("AEDA_TEST_ICEWALL"), reason="pinned ORFS image opt-in")
def test_real_chip_routes_reuses_and_invalidates(tmp_path: Path) -> None:
    placement = tmp_path / "placement"
    config = tmp_path / "chip.toml"
    config.write_bytes(CHIP.read_bytes())
    output = tmp_path / "chip"
    proposal = plan(PLACEMENT, placement)
    approve(placement, proposal["plan_digest"])
    reference = run(placement)
    first = assemble(placement, config, output)
    assert first["gates"]["database_signal_connectivity"]["status"] == "PASS"
    assert first["gates"]["signal_routing"]["status"] == "PASS"
    assert first["gates"]["power_connectivity"]["status"] == "FAIL"
    assert first["gates"]["pad_gds_views"]["status"] == "BLOCKED"
    assert first["detailed_router_violations"] == 0
    assert len(first["routed_signal_connections"]) == 2
    assert first["acceptance"] == "BLOCKED"
    assert assemble(placement, config, output)["stages"] == first["stages"]

    route = first["stages"]["route"]
    def_path = output / Path(route["receipt"]).parent / "routed.def"
    def_path.write_text("bad")
    rerun = assemble(placement, config, output)
    assert rerun["stages"]["connect"] == first["stages"]["connect"]
    assert rerun["stages"]["route"]["attempt"] != route["attempt"]
    assert rerun["stages"]["route"]["outputs"]["routed.def"] == route["outputs"]["routed.def"]

    config.write_text(CHIP.read_text().replace('"pad_y"', '"chip_y"'))
    mapped = assemble(placement, config, output)
    assert mapped["stages"]["lef_abstracts"] == first["stages"]["lef_abstracts"]
    assert mapped["stages"]["connect"]["key"] != first["stages"]["connect"]["key"]
    assert mapped["stages"]["route"]["key"] != first["stages"]["route"]["key"]

    config.write_text(config.read_text().replace('max_layer = "met4"', 'max_layer = "met5"'))
    changed_route = assemble(placement, config, output)
    assert changed_route["stages"]["connect"] == mapped["stages"]["connect"]
    assert changed_route["stages"]["route"]["key"] != mapped["stages"]["route"]["key"]

    config.write_text(config.read_text().replace('"required"', '"excluded"'))
    changed_policy = assemble(placement, config, output)
    assert changed_policy["stages"] == changed_route["stages"]
    assert changed_policy["signoff_policy_sha256"] != changed_route["signoff_policy_sha256"]
    assert changed_policy["gates"]["sta"]["status"] == "SKIPPED"
    assert changed_policy["acceptance"] == "BLOCKED"

    (placement / "io-attempts" / reference["attempt"] / "placement.def").write_text("changed")
    stale = assemble(placement, config, output)
    assert stale["gates"]["pad_placement"]["status"] == "BLOCKED"
    assert stale["gates"]["database_signal_connectivity"]["status"] == "BLOCKED"


@pytest.mark.skipif(not os.environ.get("AEDA_TEST_CHIP_VIEWS"), reason="upstream SKY130 GDS opt-in")
def test_real_reopen_upstream_gds(tmp_path: Path) -> None:
    placement = tmp_path / "placement"
    proposal = plan(PLACEMENT, placement)
    approve(placement, proposal["plan_digest"])
    run(placement)
    source = Path(os.environ["AEDA_TEST_CHIP_VIEWS"]).resolve()
    config = load(source)
    assert config.pad_liberty is not None and config.pad_gds
    for view in (*config.pad_gds, config.pad_liberty):
        destination = tmp_path / view.relative_to(source.parent)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(view, destination)
    manifest = tmp_path / "chip.toml"
    manifest.write_bytes(source.read_bytes())
    root = tmp_path / "chip"
    chip = assemble(placement, manifest, root)
    assert chip["gates"]["pad_gds_views"]["status"] == "PASS"
    assert chip["gates"]["pad_liberty_interface"]["status"] == "PASS"
    assert chip["gates"]["chip_gds"]["status"] == "PASS"
    assert chip["gates"]["independent_reopening"]["status"] == "PASS"
    assert chip["gates"]["chip_geometry_extent"]["status"] == "FAIL"
    assert chip["reopened_layout"]["boundary"]
    assert chip["reopened_layout"]["empty_references"] == []
    assert chip["reopened_layout"]["masters"]["sky130_ef_io__corner_pad"] == 4
    assert chip["gates"]["lvs"]["status"] == "BLOCKED"

    mutable = load(manifest)
    assert mutable.pad_liberty is not None
    mutable.pad_liberty.write_text("library (bad) { }")
    bad_timing = assemble(placement, manifest, root)
    assert bad_timing["gates"]["pad_liberty_interface"]["status"] == "FAIL"
    assert bad_timing["stages"]["route"] == chip["stages"]["route"]
    assert bad_timing["stages"]["chip_gds"] == chip["stages"]["chip_gds"]

    mutable.pad_gds[-1].write_bytes(b"invalid GDS")
    bad_gds = assemble(placement, manifest, root)
    assert bad_gds["gates"]["pad_gds_views"]["status"] == "FAIL"
    assert bad_gds["stages"]["route"] == chip["stages"]["route"]
    assert bad_gds["stages"]["pad_gds_views"]["key"] != chip["stages"]["pad_gds_views"]["key"]
    assert bad_gds["gates"]["chip_gds"]["status"] == "BLOCKED"
