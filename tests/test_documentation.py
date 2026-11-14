"""Check the maintained Markdown and the published figure receipts."""

import json
import re
import shlex
from pathlib import Path
from urllib.parse import unquote, urlsplit

from autonomous_eda.cli import build_parser
from autonomous_eda.manifest import sha256

ROOT = Path(__file__).resolve().parents[1]
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
LINK = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)\)")
IMAGE = re.compile(r"<img\b[^>]*>", re.I)


def markdown_lines(path: Path) -> list[tuple[int, str]]:
    result = []
    opened = ""
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        marker = FENCE.match(line)
        if marker:
            fence = marker[1]
            if not opened:
                opened = fence
            elif fence[0] == opened[0] and len(fence) >= len(opened):
                opened = ""
        elif not opened:
            result.append((number, line))
    assert not opened, f"{path}: unclosed code fence"
    return result


def test_markdown_structure_links_and_images() -> None:
    paths = [ROOT / name for name in ("README.md", "AGENTS.md", "CONTRIBUTING.md")]
    paths.extend(sorted((ROOT / "docs").rglob("*.md")))
    paths.extend(sorted((ROOT / ".github").rglob("*.md")))
    for path in paths:
        titles = 0
        previous = 0 if ".github" not in path.parts else 1
        for number, line in markdown_lines(path):
            location = f"{path.relative_to(ROOT)}:{number}"
            assert line == line.rstrip(), f"{location}: trailing whitespace"
            heading = re.match(r"^(#{1,6})\s+\S", line)
            if heading:
                level = len(heading[1])
                assert level <= previous + 1, f"{location}: skipped heading level"
                titles += level == 1
                previous = level
            links = []
            for prefix, label, target in LINK.findall(line):
                assert label.strip(), f"{location}: empty link or image label"
                links.append(target)
            for tag in IMAGE.findall(line):
                alt = re.search(r'\balt="([^"]+)"', tag)
                src = re.search(r'\bsrc="([^"]+)"', tag)
                assert alt and src, f"{location}: image needs alt text and a source"
                links.append(src[1])
            for target in links:
                url = urlsplit(target)
                if url.scheme or url.netloc:
                    continue
                destination = (path.parent / unquote(url.path)).resolve() if url.path else path
                assert destination.is_relative_to(ROOT), f"{location}: link escapes repository"
                assert destination.exists(), f"{location}: missing {target}"
        if ".github" not in path.parts:
            assert titles == 1, f"{path}: expected one H1"


def test_published_acceptance_sources_match_figure_receipts() -> None:
    for folder in (ROOT / "docs/images/examples").glob("verification-*"):
        record = json.loads((folder / "provenance.json").read_bytes())
        assert record["images"], f"{folder}: missing provenance entries"
        for image in record["images"].values():
            assert image["run_id"] == record["run_id"]
            assert sha256(folder / image["filename"]) == image["image_sha256"]
            assert image["source_artifacts"] == ["acceptance.json"]
            assert image["source_sha256"] == [sha256(folder / "acceptance.json")]


def test_chip_figure_sources_match_run_and_receipts() -> None:
    folder = ROOT / "docs/images/examples/io-integration"
    source = folder / "chip-report.json"
    report = json.loads(source.read_bytes())
    provenance = json.loads((folder / "provenance.json").read_bytes())
    digest = sha256(source)
    gds = report["stages"]["chip_gds"]
    gds_path = (Path(gds["receipt"]).parent / "chip.gds").as_posix()
    assert provenance["local_source_artifacts"][gds_path]["sha256"] == gds["outputs"]["chip.gds"]
    assert (
        gds["outputs"]["chip.gds"]
        == report["stages"]["independent_reopening"]["inputs"]["chip.gds"]
    )
    for image in provenance["images"].values():
        assert image["run_id"] == provenance["run_id"] == digest[:32]
        assert image["image_sha256"] == sha256(folder / image["filename"])
        assert image["render_configuration"]["command"]
        assert image["render_configuration"]["top"] == report["top"]
        assert image["render_configuration"]["platform"] == report["platform"]
        sources = dict(zip(image["source_artifacts"], image["source_sha256"], strict=True))
        expected = {source.name: digest}
        if image["renderer"] == "klayout":
            expected[gds_path] = gds["outputs"]["chip.gds"]
        assert sources == expected
