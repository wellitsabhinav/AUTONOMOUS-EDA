"""Pinned experimental Sky130HD handoff for the OpenTitan AES source receipt."""

import shutil
from pathlib import Path

from .manifest import sha256

KEY = ("sky130hd", "opentitan_aes")
RTL_REVISION = "b2d662157db2802bde0e40dfc96fd0316042d116"
RTL_DIGEST = "f2adfd17f78bbdbe0ecad10a61e08d8a574aa706feb0a2c7b818775fd5f944a8"
CONFIG = """export DESIGN_NAME = aes
export DESIGN_NICKNAME = opentitan_aes
export PLATFORM = sky130hd
export VERILOG_FILES = /work/aes-input/aes.v
export SDC_FILE = /work/aes-input/block.sdc
export CORE_UTILIZATION = 35
export CORE_MARGIN = 3
export PLACE_DENSITY_LB_ADDON = 0.20
"""
SDC = """current_design aes
create_clock -name main -period 8.5 [get_ports {clk_i clk_edn_i}]
"""


def selected(platform: str, design: str) -> bool:
    return (platform, design) == KEY


def check_translated_rtl(path: Path) -> None:
    if not path.is_file():
        raise ValueError("the pinned AES translated Verilog is missing")
    if sha256(path) != RTL_DIGEST:
        raise ValueError("AES translated Verilog fails the 10A receipt SHA-256 check")


def stage_bundle(source: Path, root: Path) -> None:
    folder = root / "aes-input"
    folder.mkdir()
    shutil.copyfile(source, folder / "aes.v")
    (folder / "config.mk").write_bytes(CONFIG.encode())
    (folder / "block.sdc").write_bytes(SDC.encode())
    check_bundle(root)


def check_bundle(root: Path) -> None:
    folder = root / "aes-input"
    names = ("aes.v", "block.sdc", "config.mk")
    for name in names:
        path = folder / name
        if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError(f"AES staged input missing or outside run directory: {name}")
    check_translated_rtl(folder / "aes.v")
    if (folder / "config.mk").read_bytes() != CONFIG.encode():
        raise ValueError("AES config differs from the controller's pinned adapter")
    if (folder / "block.sdc").read_bytes() != SDC.encode():
        raise ValueError("AES SDC differs from the controller's pinned adapter")
