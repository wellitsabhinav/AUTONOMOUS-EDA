"""Summarize structural evidence from a Yosys JSON elaboration."""

from collections import Counter
from functools import cache
from typing import Any

SEQUENTIAL = {
    "$dff",
    "$dffe",
    "$dffsr",
    "$dffsre",
    "$adff",
    "$adffe",
    "$aldff",
    "$aldffe",
    "$sdff",
    "$sdffe",
    "$sdffce",
}


def summarize(top: str, modules: dict[str, Any], *, elaborated: bool) -> dict[str, object]:
    @cache
    def expanded(name: str) -> Counter[str]:
        counts = Counter({name: 1})
        for cell in modules[name].get("cells", {}).values():
            child = cell["type"]
            if child in modules:
                counts.update(expanded(child))
        return counts

    occurrence_counts = expanded(top)
    controls: dict[str, list[dict[str, object]]] = {}
    sequential_cells: Counter[str] = Counter()
    memory_modules = {}
    black_boxes = {}
    for name, count in sorted(occurrence_counts.items()):
        module = modules[name]
        attributes = module.get("attributes", {})
        if any(int(attributes.get(flag, "0"), 2) for flag in ("blackbox", "whitebox")):
            black_boxes[name] = count
        if memories := module.get("memories"):
            memory_modules[name] = {
                "module_instances": count,
                "memories": {
                    memory: {"width": value["width"], "depth": value["size"]}
                    for memory, value in memories.items()
                },
            }
        if not elaborated:
            continue
        # Prefer visible net names over auto-generated aliases. Report control nets
        # within each definition, since the same module may use different domains.
        aliases = {
            tuple(value["bits"]): net
            for net, value in sorted(module.get("netnames", {}).items(), reverse=True)
            if not value.get("hide_name")
        }
        observed: Counter[tuple[str, tuple[int | str, ...], str | None]] = Counter()
        for cell in module.get("cells", {}).values():
            kind = cell["type"]
            if kind not in SEQUENTIAL:
                continue
            sequential_cells[kind] += count
            for pin in ("CLK", "ARST", "SRST", "ALOAD", "SET", "CLR"):
                if bits := cell["connections"].get(pin):
                    observed[pin, tuple(bits), cell["parameters"].get(f"{pin}_POLARITY")] += 1
        if observed:
            controls[name] = [
                {
                    "pin": pin,
                    "bits": list(bits),
                    "net": aliases.get(bits),
                    "polarity": int(polarity, 2) if polarity else None,
                    "cells_in_definition": amount,
                }
                for (pin, bits, polarity), amount in sorted(observed.items(), key=str)
            ]

    return {
        "schema_version": 1,
        "scope": "structural RTL elaboration" if elaborated else "generic Yosys synthesis",
        "module_definition_count": len(occurrence_counts),
        "hierarchy_instance_count": sum(occurrence_counts.values()) - 1,
        "module_instances": dict(sorted(occurrence_counts.items())),
        "primitive_cell_count": sum(
            count
            * sum(cell["type"] not in modules for cell in modules[name].get("cells", {}).values())
            for name, count in occurrence_counts.items()
        ),
        "sequential_cells": dict(sorted(sequential_cells.items())) if elaborated else None,
        "clock_and_reset_controls": controls if elaborated else None,
        "clock_and_reset_scope": "local sequential-cell connections; no domain or CDC proof",
        "inferred_memories": memory_modules if elaborated else None,
        "blackbox_module_instances": black_boxes,
        "gates": {
            "elaboration": "VERIFIED",
            "clock_and_reset_inventory": "AVAILABLE" if controls else "UNSUPPORTED",
            "generated_clock_qualification": "BLOCKED",
            "physical_and_timing_views": "BLOCKED",
            "physical_qualification": "BLOCKED",
        },
    }
