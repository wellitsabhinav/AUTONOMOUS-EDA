"""Compact tests for the abstract placement plan and evidence invalidation."""

import copy
import os
from pathlib import Path
from typing import Any

import pytest

from autonomous_eda.io_ring import (
    approve,
    checked_config,
    inspect_def,
    load_config,
    plan,
    report,
    run,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "io" / "placement.toml"


def raw_config() -> dict[str, Any]:
    config = load_config(EXAMPLE)
    config["pads"] = [
        {key: value for key, value in pad.items() if value is not None} for pad in config["pads"]
    ]
    return config


@pytest.mark.parametrize(
    "change",
    [
        "die",
        "corner",
        "fillers",
        "master",
        "kind",
        "mirror",
        "duplicate",
        "overlap",
        "supply",
        "direction",
    ],
)
def test_invalid_io_config(change: str) -> None:
    config = copy.deepcopy(raw_config())
    pads = config["pads"]
    signal = next(pad for pad in pads if pad["kind"] == "input")
    if change == "die":
        config["width_um"] = 400
    elif change == "corner":
        config["corner"] = "missing"
    elif change == "fillers":
        config["fillers"] = []
    elif change == "master":
        signal["master"] = "missing"
    elif change == "kind":
        signal["kind"] = "unsupported"
    elif change == "mirror":
        signal["mirror"] = True
    elif change == "duplicate":
        pads.append(dict(signal))
    elif change == "overlap":
        pads.append({**signal, "name": "other", "port": "other", "location_um": 555})
    elif change == "supply":
        next(pad for pad in pads if pad["kind"] == "power")["port"] = "power"
    else:
        signal["port"] = "a[0]"
    with pytest.raises(ValueError):
        checked_config(config)


def test_core_only_plan_is_not_applicable(tmp_path: Path) -> None:
    config = tmp_path / "core.toml"
    config.write_text("schema_version=1\nenabled=false\n")
    root = tmp_path / "disabled"
    planned = plan(config, root)
    with pytest.raises(ValueError, match="approval digest"):
        approve(root, "0" * 64)
    approve(root, planned["plan_digest"])
    assert set(run(root)["gates"].values()) == {"NOT_APPLICABLE"}


def test_wrong_observed_orientation() -> None:
    config = load_config(EXAMPLE)
    text = "UNITS DISTANCE MICRONS 1000 ;\n"
    text += "- pad_in sky130_ef_io__gpiov2_pad_wrapped + FIXED ( 550000 989035 ) N ;"
    assert inspect_def(text, config)["placement_matches"] is False


@pytest.mark.skipif(
    not os.environ.get("AEDA_TEST_ICEWALL"),
    reason="pinned ICeWall integration opt-in",
)
def test_real_icewall_placement_resume_and_changed_def(tmp_path: Path) -> None:
    root = tmp_path / "io"
    planned = plan(EXAMPLE, root)
    approve(root, planned["plan_digest"])
    first = run(root)
    assert first["gates"]["PAD_PLACEMENT_LEGAL"] == "PASSED"
    assert first["gates"]["PAD_CORNER_COMPLETENESS"] == "PASSED"
    assert first["abstract_fillers_present"]
    assert first["gates"]["PAD_GDS_REOPEN"] == "BLOCKED"
    assert first["acceptance"] == "BLOCKED"
    assert run(root)["attempt"] == first["attempt"]
    (root / "io-attempts" / first["attempt"] / "placement.def").write_text("changed")
    assert report(root)["gates"]["PAD_PLACEMENT_LEGAL"] == "BLOCKED"
    assert run(root)["attempt"] != first["attempt"]
