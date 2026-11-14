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


def test_manual_covers_public_cli_and_example_syntax() -> None:
    parser = build_parser()
    help_text = parser.format_usage()
    listed = re.search(r"\{([\w,-]+)\}", help_text)
    assert listed is not None
    reference = (ROOT / "docs/CLI_REFERENCE.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `([\w-]+)` \|", reference, re.M))
    assert documented == set(listed[1].split(","))

    for name in ("README.md", "docs/tutorials/GCD_WALKTHROUGH.md", "docs/PHYSICAL_WORKSPACE.md"):
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
            if line.startswith("autonomous-eda "):
                parser.parse_args(shlex.split(line)[1:])


def test_readme_navigation_covers_all_sections() -> None:
    lines = [line for _, line in markdown_lines(ROOT / "README.md")]
    headings = [line[3:] for line in lines if line.startswith("## ")]
    anchors = [heading.lower().replace(" ", "-") for heading in headings]
    navigation = [
        target[1:]
        for line in lines
        if line.startswith("- [")
        for _, _, target in LINK.findall(line)
        if target.startswith("#")
    ]
    assert navigation == anchors


def test_gcd_walkthrough_publication_matches_recorded_evidence() -> None:
    root = ROOT / "docs/evidence/gcd-walkthrough"
    receipt = json.loads((root / "receipt.json").read_bytes())
    for name, digest in receipt["publication_sha256"].items():
        assert sha256(root / name) == digest
    assert receipt["run_json_sha256"] == receipt["resume_json_sha256"]
    assert receipt["verification_exit_code"] == 1

    plan = json.loads((root / "plan.json").read_bytes())
    attempts = receipt["attempts"]
    assert [attempt["stage"] for attempt in attempts] == [stage["name"] for stage in plan]
    assert all(attempt["state"] == "passed" for attempt in attempts)
    assert all(
        set(stage["outputs"]) <= attempt["artifacts"].keys()
        for stage, attempt in zip(plan, attempts, strict=True)
    )
    finish = attempts[-1]["artifacts"]
    metric = receipt["setup_metric"]
    final_report = root / Path(metric["artifact"]).name
    assert sha256(final_report) == finish[metric["artifact"]]
    assert json.loads(final_report.read_bytes())[metric["reference"]] == metric["value"] < 0

    verification = (root / "verification.txt").read_text(encoding="utf-8")
    assert receipt["run_id"] in verification
    assert f"Decision `{receipt['verification_decision_sha256']}`" in verification
    assert f"worst slack {metric['value']:g} ns" in verification
    tutorial = (ROOT / "docs/tutorials/GCD_WALKTHROUGH.md").read_text(encoding="utf-8")
    excerpts = re.findall(r"~~~text\n(.+?)\n~~~", tutorial, re.S)
    for name in ("verification.txt", "diagnosis.txt"):
        capture = (root / name).read_text(encoding="utf-8")
        assert any(excerpt in capture for excerpt in excerpts), name

    figures = ROOT / "docs/images/examples/gcd-walkthrough"
    record = json.loads((figures / "provenance.json").read_bytes())["images"]["layout_overview"]
    assert record["run_id"] == receipt["run_id"]
    assert record["image_sha256"] == sha256(figures / record["filename"])
    assert record["source_sha256"] == [finish[path] for path in record["source_artifacts"]]
