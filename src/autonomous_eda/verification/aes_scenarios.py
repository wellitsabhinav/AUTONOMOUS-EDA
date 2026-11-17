"""Native AES stimulus schedules and independently checked result records."""

from __future__ import annotations

import re
from collections.abc import Mapping
from itertools import zip_longest
from pathlib import Path
from typing import Any

from ..manifest import fingerprint

PROFILES: dict[str, dict[str, int]] = {
    "common": {},
    "phase_offset": {"edn_phase": 3, "edn_delay": 4},
    "independent": {
        "clk_half": 4,
        "clk_phase": 1,
        "edn_half": 7,
        "edn_phase": 2,
        "reset_cycles": 8,
        "edn_release_cycles": 2,
        "shadow_release_cycles": 1,
        "edn_delay": 9,
        "edn_jitter": 8,
        "tl_jitter": 3,
    },
    "sideload": {
        "edn_half": 7,
        "edn_phase": 2,
        "edn_delay": 2,
        "tl_jitter": 2,
        "exercise_sideload": 1,
    },
    "errors": {"edn_half": 6, "edn_phase": 1, "exercise_errors": 1},
}
DEFAULTS = {
    "clk_half": 5,
    "clk_phase": 0,
    "edn_half": 5,
    "edn_phase": 0,
    "reset_cycles": 6,
    "edn_release_cycles": 0,
    "shadow_release_cycles": 0,
    "edn_delay": 0,
    "edn_jitter": 0,
    "tl_jitter": 0,
}

# FIPS 197 examples and the AES-128 all-zero key/block known answer, in bus byte order.
PLAIN = "ffeeddccbbaa99887766554433221100"
CIPHER128 = "5ac5b47080b7cdd830047b6ad8e0c469"
CIPHER256 = "8960494b9049fceabf456751cab7a28e"
SOFTWARE = (("software_encrypt_128", CIPHER128), ("software_decrypt_128", PLAIN))
SIDELOAD = (
    ("sideload_encrypt_128", CIPHER128),
    ("sideload_pins_changed_old_key", CIPHER128),
    ("sideload_update_128", "2e2b34ca59fa4c883b2c8aefd44be966"),
    ("sideload_encrypt_192", "91710deca070af6ee0df4c86a47ca9dd"),
    ("sideload_sanitized_encrypt_256", CIPHER256),
    ("sideload_decrypt_256", PLAIN),
    ("software_encrypt_128", CIPHER128),
)
CASES = {
    "sideload": ["sideload_invalid_key", "active_reset"],
    "errors": [
        "shadow_update_error",
        "fatal_alert_test_handshake",
        "command_integrity_fault",
        "error_reset_recovery",
    ],
}
TRACE = re.compile(r"AEDA_AES_(TRACE|EDN) (\d+) ([0-9a-f]+)")
VECTOR = re.compile(r"AEDA_AES_VECTOR (\w+) ([0-9a-f]{32}) ([0-9a-f]{32})")
EDGE = re.compile(r"AEDA_AES_EDGE (CORE|EDN) ([01]) (\d+)")


def trace(lines: list[str]) -> dict[str, list[tuple[int, str]]]:
    rows: dict[str, list[tuple[int, str]]] = {"TRACE": [], "EDN": []}
    for line in lines:
        if match := TRACE.fullmatch(line):
            rows[match[1]].append((int(match[2]), match[3]))
    return rows


def scenario_observations(
    lines: list[str], name: str, config: Mapping[str, int]
) -> tuple[str, str, dict[str, Any]]:
    edges = trace(lines)
    schedule = {
        domain: [
            (int(m[2]), int(m[3]))
            for line in lines
            if (m := EDGE.fullmatch(line)) and m[1] == domain
        ]
        for domain in ("CORE", "EDN")
    }
    facts: dict[str, Any] = {
        "trace_sha256": fingerprint(edges),
        "core_edges": len(edges["TRACE"]),
        "edn_edges": len(edges["EDN"]),
        "first_edges_ns": schedule,
        "vectors": [match.groups() for line in lines if (match := VECTOR.fullmatch(line))],
        "cases": [
            line.removeprefix("AEDA_AES_CASE ")
            for line in lines
            if line.startswith("AEDA_AES_CASE ")
        ],
    }
    counts = [line.split()[1:] for line in lines if line.startswith("AEDA_AES_COUNTS ")]
    if len(counts) != 1 or len(counts[0]) != 3 or not all(x.isdecimal() for x in counts[0]):
        return "INFRASTRUCTURE_ERROR", "Missing complete TL-UL and EDN counts", facts
    tl, transfers, stalls = map(int, counts[0])
    facts.update(tl_transactions=tl, edn_transfers=transfers, edn_stall_edges=stalls)
    expected: tuple[tuple[str, str], ...] = SOFTWARE
    if name == "sideload":
        expected += SIDELOAD
    elif name == "errors":
        expected += (SOFTWARE[1],)
    if facts["vectors"] != [(label, value, value) for label, value in expected]:
        return "FAIL", "Known-answer vector inventory or data differ", facts
    if facts["cases"] != ["idle_reset", "illegal_register", *CASES.get(name, [])]:
        return "INFRASTRUCTURE_ERROR", "Required scenario coverage is incomplete", facts
    if any(not rows or any(i != n for n, (i, _) in enumerate(rows)) for rows in edges.values()):
        return "INFRASTRUCTURE_ERROR", "Incomplete core or EDN edge sequence", facts
    for domain, clock in (("CORE", "clk"), ("EDN", "edn")):
        half, phase = config[f"{clock}_half"], config[f"{clock}_phase"]
        if schedule[domain] != [(0, phase + half), (1, phase + 3 * half)]:
            return "FAIL", f"{domain} clock schedule differs from the requested schedule", facts
    if not tl or not transfers or stalls < transfers * config["edn_delay"]:
        return "FAIL", "TL-UL transfers or configured EDN waits did not occur", facts
    return "PASS", "Required protocol checks and known-answer vectors passed", facts


def first_difference(original: Path, translated: Path) -> dict[str, Any] | None:
    left = trace(original.read_text(encoding="utf-8").splitlines())
    right = trace(translated.read_text(encoding="utf-8").splitlines())
    for domain in left:
        for index, (a, b) in enumerate(zip_longest(left[domain], right[domain])):
            if a != b:
                return {"domain": domain, "edge": index, "original": a, "translated": b}
    return None
