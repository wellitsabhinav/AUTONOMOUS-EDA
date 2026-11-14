"""Manifest and fixed pin roles for the SKY130 ICeWall chip probe."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .manifest import identifier, relative_path
from .mcp.snapshot import ordinary
from .verification.policy import table

Status = Literal["PASS", "FAIL", "BLOCKED", "SKIPPED"]
LAYERS = ("met1", "met2", "met3", "met4", "met5")
CONTROL_PINS = (
    "ANALOG_EN",
    "ANALOG_POL",
    "ANALOG_SEL",
    "DM[0]",
    "DM[1]",
    "DM[2]",
    "ENABLE_H",
    "ENABLE_INP_H",
    "ENABLE_VDDA_H",
    "ENABLE_VDDIO",
    "ENABLE_VSWITCH_H",
    "HLD_H_N",
    "HLD_OVR",
    "IB_MODE_SEL",
    "INP_DIS",
    "OE_N",
    "SLOW",
    "VTRIP_SEL",
)
SIGNAL_PINS = {"input": "IN", "output": "OUT"}
SUPPLY_PINS = {"power": "VCCD", "ground": "VSSD"}


@dataclass(frozen=True)
class Mapping:
    pad: str
    core_pin: str
    external_port: str


@dataclass(frozen=True)
class ChipConfig:
    mappings: tuple[Mapping, ...]
    route: bool
    min_layer: str
    max_layer: str
    provenance: str
    pad_gds: tuple[Path, ...]
    pad_liberty: Path | None
    signoff: Literal["required", "excluded"]


def load(path: Path) -> ChipConfig:
    ordinary(path, directory=False)
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("chip configuration exceeds 64 KiB")
    parsed = table(
        tomllib.loads(raw.decode()),
        {"schema_version", "signals", "routing", "library", "signoff"},
        "chip configuration",
    )
    if type(parsed.get("schema_version")) is not int or parsed["schema_version"] != 1:
        raise ValueError("chip configuration requires schema_version = 1")
    signals = parsed.get("signals")
    if not isinstance(signals, list) or not 1 <= len(signals) <= 64:
        raise ValueError("provide 1 to 64 signal pad mappings")
    mappings = []
    for item in signals:
        entry = table(item, {"pad", "core_pin", "external_port"}, "signal pad mapping")
        mappings.append(
            Mapping(
                *(identifier(entry.get(key), key) for key in ("pad", "core_pin", "external_port"))
            )
        )
    for names in ([m.pad for m in mappings], [m.external_port for m in mappings]):
        if len(names) != len(set(names)):
            raise ValueError("duplicate pad or external port mapping")

    routing = table(parsed.get("routing", {}), {"enabled", "min_layer", "max_layer"}, "routing")
    enabled = routing.get("enabled", True)
    low, high = routing.get("min_layer", "met1"), routing.get("max_layer", "met4")
    if type(enabled) is not bool or low not in LAYERS or high not in LAYERS:
        raise ValueError("routing requires a Boolean and SKY130 metal layers met1 through met5")
    if LAYERS.index(low) > LAYERS.index("met2") or LAYERS.index(high) < LAYERS.index("met2"):
        raise ValueError("routing layer range must contain the met2 pad pin access layer")

    library = table(parsed.get("library", {}), {"provenance", "gds", "liberty"}, "pad library")
    provenance = library.get("provenance", "ORFS pinned sky130io abstracts")
    if not isinstance(provenance, str) or not provenance.strip() or len(provenance) > 512:
        raise ValueError("pad library provenance must identify the source revision")
    if library.keys() & {"gds", "liberty"} and "provenance" not in library:
        raise ValueError("external pad views require explicit library provenance")
    parent = path.resolve().parent
    raw_gds = library.get("gds", [])
    if not isinstance(raw_gds, list) or len(raw_gds) > 16:
        raise ValueError("library.gds must list at most 16 GDS files")
    gds = tuple(parent / relative_path(name, "pad GDS", (".gds",)) for name in raw_gds)
    if len(set(gds)) != len(gds):
        raise ValueError("duplicate pad GDS source")
    liberty = (
        parent / relative_path(library["liberty"], "pad Liberty", (".lib",))
        if "liberty" in library
        else None
    )
    for source in (*gds, liberty):
        if source is not None and not source.resolve().is_relative_to(parent):
            raise ValueError("external pad view escapes the manifest directory")
    policy = parsed.get("signoff", "required")
    if policy not in ("required", "excluded"):
        raise ValueError("signoff must be required or explicitly excluded")
    return ChipConfig(
        tuple(sorted(mappings, key=lambda m: m.pad)),
        enabled,
        low,
        high,
        provenance,
        gds,
        liberty,
        policy,
    )
