import hashlib
import importlib
import json
import re
from pathlib import Path

import pytest

from autonomous_eda.visual_artifacts import MANIFEST, gallery, png_size, render_run

pytest.importorskip("matplotlib")
pytest.importorskip("PIL")


@pytest.fixture
def visual_run(tmp_path: Path) -> Path:
    variant = "sky130hd/gcd/base"
    files = {
        f"logs/{variant}/6_report.json": b'{"finish__timing__setup__ws":-1.20488}',
        f"results/{variant}/1_synth.sdc": b"create_clock -name core_clock -period 1.1\n",
        f"reports/{variant}/6_finish.rpt": b"unsupported timing report",
    }
    reference = json.loads(Path("docs/evidence/gcd-sky130hd-index.json").read_bytes())
    reference["sha256"] = {}
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        reference["sha256"][name] = hashlib.sha256(content).hexdigest()
    (tmp_path / "reference.json").write_text(json.dumps(reference), encoding="utf-8")
    return tmp_path


def test_negative_timing_figure_and_stale_source(visual_run: Path) -> None:
    value = render_run(visual_run, "reports")
    images = value["images"]
    assert "timing_setup_summary" in images
    assert "timing_hold_summary" not in images
    assert value["unavailable"]["timing_hold_summary"] == "No verified source measurement"
    row = images["timing_setup_summary"]
    path = visual_run / "visualizations" / row["filename"]
    assert path.name == "timing_setup_summary.png"
    assert png_size(path) == (1700, 1054)
    assert row["renderer"] == "matplotlib-agg"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == row["image_sha256"]
    assert gallery(visual_run)["images"]["timing_setup_summary"]["evidence_status"] == "VERIFIED"
    (visual_run / row["source_artifacts"][0]).write_bytes(b'{"finish__timing__setup__ws":0}')
    assert gallery(visual_run)["images"]["timing_setup_summary"]["evidence_status"] == "STALE"


def test_layout_unavailable_and_empty_png(visual_run: Path) -> None:
    report = render_run(visual_run, "layout")
    assert not report["images"]
    assert "missing" in report["unavailable"]["layout_overview"]
    blank = visual_run / "blank.png"
    pillow = importlib.import_module("PIL.Image")
    pillow.new("RGB", (80, 80), (25, 25, 25)).save(blank)
    with pytest.raises(ValueError, match="empty"):
        png_size(blank)


def test_manifest_traversal_is_rejected(visual_run: Path) -> None:
    output = visual_run / "visualizations"
    output.mkdir()
    (output / MANIFEST).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "fixture",
                "images": {"../outside": {"filename": "../outside.png"}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        gallery(visual_run)


def test_published_gallery_provenance_and_links() -> None:
    for document in (Path("README.md"), Path("docs/EXAMPLE_GALLERY.md")):
        body = document.read_text(encoding="utf-8")
        links = re.findall(r'(?:\]\(|src=")([^)"#]+)', body)
        assert links
        assert all(
            (document.parent / link).is_file()
            for link in links
            if link.endswith((".png", ".md", ".json")) and "://" not in link
        )
    for folder in Path("docs/images/examples").iterdir():
        manifest = json.loads((folder / "provenance.json").read_bytes())
        for entry in manifest["images"].values():
            image = folder / entry["filename"]
            assert hashlib.sha256(image.read_bytes()).hexdigest() == entry["image_sha256"]
            assert png_size(image) == (entry["image_width"], entry["image_height"])
