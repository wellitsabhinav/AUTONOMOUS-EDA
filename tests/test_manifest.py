import shutil
from pathlib import Path

import pytest

from autonomous_eda.discovery import inspect_rtl
from autonomous_eda.manifest import ORFSManifest, RTLManifest, load_manifest


def test_reference_manifest() -> None:
    manifest = load_manifest(Path("configs/ibex-sky130hd.toml"))
    assert isinstance(manifest, ORFSManifest)
    assert manifest.top == "ibex_core"
    assert manifest.platform == "sky130hd"
    assert manifest.clock_period_ns == 8.0


def test_rtl_manifest_preserves_source_order(tmp_path: Path) -> None:
    for name in ("package.sv", "top.sv"):
        (tmp_path / name).touch()
    path = tmp_path / "design.toml"
    path.write_text(
        'schema_version=1\nbackend="yosys"\ntop="test"\nsources=["package.sv","top.sv"]',
        encoding="utf-8",
    )
    manifest = load_manifest(path)
    assert isinstance(manifest, RTLManifest)
    assert [source.name for source in manifest.sources] == ["package.sv", "top.sv"]


@pytest.mark.parametrize("source", ["../other.sv", "file;exit.sv", "missing.sv"])
def test_invalid_sources_are_rejected(tmp_path: Path, source: str) -> None:
    path = tmp_path / "design.toml"
    path.write_text(
        f'schema_version=1\nbackend="yosys"\ntop="test"\nsources=["{source}"]',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_manifest(path)


def test_input_fingerprint_covers_order_and_dependencies(tmp_path: Path) -> None:
    (tmp_path / "inc").mkdir()
    header = tmp_path / "inc" / "w.vh"
    header.write_text("`define W 4", encoding="utf-8")
    for name in ("top.sv", "generated.sv", "top.sdc"):
        (tmp_path / name).touch()
    path = tmp_path / "design.toml"
    text = (
        'schema_version=1\nbackend="yosys"\ntop="top"\n'
        'sources=["generated.sv","top.sv"]\ninclude_dirs=["inc"]\n'
        'generated_sources=["generated.sv"]\nconstraints=["top.sdc"]\n'
        '[defines]\nFLAG="1"\n[parameters]\nWIDTH=4\n'
    )
    path.write_text(text, encoding="utf-8")
    first = load_manifest(path)
    assert isinstance(first, RTLManifest)
    digest = first.fingerprint()
    path.write_text(
        text.replace('"generated.sv","top.sv"', '"top.sv","generated.sv"'), encoding="utf-8"
    )
    reordered = load_manifest(path)
    assert isinstance(reordered, RTLManifest)
    assert reordered.fingerprint() != digest
    header.write_text("`define W 8", encoding="utf-8")
    assert first.fingerprint() != digest


@pytest.mark.skipif(shutil.which("yosys") is None, reason="requires a real Yosys executable")
def test_real_elaboration_and_unresolved_dependency(tmp_path: Path) -> None:
    manifest = load_manifest(Path("examples/counter.toml"))
    assert isinstance(manifest, RTLManifest)
    netlist = tmp_path / "design.json"
    result = inspect_rtl(manifest, netlist)
    assert result["top"] == "counter"
    assert netlist.stat().st_size > 0

    source = tmp_path / "missing.sv"
    source.write_text("module top(input a); unknown u(a); endmodule", encoding="utf-8")
    with pytest.raises(ValueError, match="Yosys elaboration failed.*unknown"):
        inspect_rtl(RTLManifest("top", (source,)))
