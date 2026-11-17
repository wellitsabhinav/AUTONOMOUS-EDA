"""Check the maintained Markdown and the published figure receipts."""

import argparse
import json
import re
import shlex
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

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


def parser_actions(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def parse_cli_example(parser: argparse.ArgumentParser, command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    tokens = []
    for token in lexer:
        if token in {"|", "||", "&", "&&", ">", ">>", "<", ";"}:
            break
        tokens.append(token)
    if "--help" in tokens or "--version" in tokens:
        with pytest.raises(SystemExit) as raised:
            parser.parse_args(tokens)
        assert raised.value.code == 0, command
    else:
        parser.parse_args(tokens)
    for token in tokens:
        if token.startswith(("examples/", "configs/")):
            assert (ROOT / token).exists(), command
    return tokens


def test_manual_covers_public_cli_and_example_syntax() -> None:
    parser = build_parser()
    public = parser_actions(parser)
    reference = (ROOT / "docs/CLI_REFERENCE.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `([\w-]+)` \|", reference, re.M))
    assert documented == public.keys()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    cli = readme.split("\n## CLI reference\n", 1)[1].split("\n## ", 1)[0]
    examples = re.findall(r"^\| `([a-z][\w-]*)` \| `([^`]+)`<br>", cli, re.M)
    assert {family for family, _ in examples} == public.keys()
    for family, command in examples:
        assert parse_cli_example(parser, command)[0] == family

    for name in ("README.md", "docs/tutorials/GCD_WALKTHROUGH.md", "docs/PHYSICAL_WORKSPACE.md"):
        manual = (ROOT / name).read_text(encoding="utf-8").replace("\\\n", " ")
        for line in manual.splitlines():
            if line.startswith("autonomous-eda "):
                parse_cli_example(parser, line.removeprefix("autonomous-eda "))


def test_readme_budget_and_report_flags_match_parser_scopes() -> None:
    flags: dict[str, set[str]] = {}

    def collect(group: argparse.ArgumentParser, path: str = "") -> None:
        for action in group._actions:
            for option in action.option_strings:
                flags.setdefault(option, set()).add(path)
        for name, child in parser_actions(group).items():
            collect(child, f"{path} {name}".strip())

    collect(build_parser())
    scopes = {
        "--memory-mib": {"run", "resources plan", "resources retry", "optimize plan"},
        "--wall-clock-seconds": {"run", "resume"},
        "--max-artifact-mib": {"run", "resume"},
        "--max-tool-processes": {"run", "resume"},
        "--max-retries": {"run", "resume"},
        "--profile": {"verify", "report", "resume", "optimize frontier"},
        "--coverage": {"verify"},
        "--decks": {"verify"},
        "--render": {"verify", "optimize run", "optimize resume"},
        "--markdown": {"verify", "report", "resume"},
        "--engineering": {"report"},
    }
    assert {flag: flags[flag] for flag in scopes} == scopes
    lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    for flag, commands in scopes.items():
        row = next(line for line in lines if line.startswith(f"| `{flag}`"))
        assert set(re.findall(r"`([^`]+)`", row.split("|")[2])) == commands


def github_anchor(title: str) -> str:
    return re.sub(r"[^\w -]", "", title.lower()).replace(" ", "-")


def heading_anchors(path: Path) -> list[str]:
    headings: list[str] = []
    for _, line in markdown_lines(path):
        match = re.match(r"^#{1,6} (.+)$", line)
        if match:
            anchor = github_anchor(match[1])
            candidate = anchor
            suffix = 0
            while candidate in headings:
                suffix += 1
                candidate = f"{anchor}-{suffix}"
            headings.append(candidate)
    return headings


def test_readme_navigation_covers_all_sections() -> None:
    readme = ROOT / "README.md"
    lines = [line for _, line in markdown_lines(readme)]
    anchors = [github_anchor(line[3:]) for line in lines if line.startswith("## ")]
    toc = lines[lines.index("**Contents**") + 1 : lines.index("## Installation and quickstart")]
    navigation = [
        target[1:]
        for line in toc
        if line.startswith("- [")
        for _, _, target in LINK.findall(line)
        if target.startswith("#")
    ]
    assert navigation == anchors
    for path in [readme, *(ROOT / "docs").rglob("*.md")]:
        for number, line in markdown_lines(path):
            for _, _, target in LINK.findall(line):
                url = urlsplit(target)
                if url.scheme or url.netloc or not url.fragment:
                    continue
                destination = (path.parent / unquote(url.path)).resolve() if url.path else path
                if path == readme or destination == readme:
                    assert unquote(url.fragment) in heading_anchors(destination), (
                        f"{path.relative_to(ROOT)}:{number}: {target}"
                    )


def test_readme_fences_and_tables() -> None:
    readme = ROOT / "README.md"
    opened = ""
    for line in readme.read_text(encoding="utf-8").splitlines():
        match = FENCE.match(line)
        if not match:
            continue
        marker = match[1]
        if not opened:
            assert re.fullmatch(r"\w+", line[match.end() :]), line
            opened = marker
        elif marker[0] == opened[0] and len(marker) >= len(opened):
            assert line[match.end() :] == ""
            opened = ""
    assert not opened

    width = 0
    for _, line in markdown_lines(readme):
        if not line.startswith("|"):
            width = 0
            continue
        assert line.endswith("|")
        cells = line[1:-1].split("|")
        if not width:
            width = len(cells)
            assert width >= 2
        assert len(cells) == width, line


def test_readme_figure_captions_bind_published_hashes() -> None:
    lines = [line for _, line in markdown_lines(ROOT / "README.md")]
    seen = set()
    for index, line in enumerate(lines):
        for tag in IMAGE.findall(line):
            match = re.search(r'\bsrc="([^"]+)"', tag)
            assert match is not None
            image = ROOT / match[1]
            assert image not in seen, image
            seen.add(image)
            caption = lines[index + 2]
            assert lines[index + 1] == "" and caption.startswith("*")
            receipts = [
                ROOT / target
                for _, _, target in LINK.findall(caption)
                if target.endswith(
                    (
                        "/provenance.json",
                        "/visualization_manifest.json",
                        "/resource-browser-check.json",
                        "/verified-correction.json",
                    )
                )
            ]
            assert len(receipts) == 1, caption
            record = json.loads(receipts[0].read_bytes())
            if "stages" in record:
                digest = record["stages"][image.stem]["png_sha256"]
            elif "images" in record:
                entry = next(
                    item for item in record["images"].values() if item["filename"] == image.name
                )
                digest = entry["image_sha256"]
            elif "browser_review" in record:
                assert record["browser_review"]["file"] == image.name
                digest = record["browser_review"]["image_sha256"]
            else:
                digest = record["image_sha256"]
            assert sha256(image) == digest, image

    stages = ROOT / "docs/images/gcd-stage-snapshots"
    assert {
        stages / f"{stage}.png"
        for stage in ("floorplan", "place", "cts", "grt", "route", "finish", "gds")
    } <= seen
    synthesis = ROOT / "docs/images/readme"
    assert synthesis / "gcd-synthesis.png" in seen
    record = json.loads((synthesis / "provenance.json").read_bytes())
    source = synthesis / record["source_file"]
    receipt = json.loads((ROOT / "docs/evidence/gcd-walkthrough/receipt.json").read_bytes())
    assert record["run_id"] == receipt["run_id"]
    assert record["stage"] == "synth"
    assert (
        sha256(source)
        == record["source_sha256"]
        == receipt["attempts"][0]["artifacts"][record["source_artifact"]]
    )
    assert set(record["selected_keys"]) <= json.loads(source.read_bytes()).keys()


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


def test_opentitan_publication_matches_the_resolved_source_receipts() -> None:
    root = ROOT / "docs/evidence/opentitan-aes"
    receipt = json.loads((root / "receipt.json").read_bytes())
    source = json.loads((root / "resolved.json").read_bytes())
    inspection = json.loads((root / "inspect.json").read_bytes())
    for name, digest in receipt["published_sha256"].items():
        assert sha256(root / name) == digest
    assert receipt["manifest_sha256"] == source["manifest_sha256"] == inspection["manifest_sha256"]
    assert (
        receipt["source_resolution"]["independent_build_receipts_sha256"]
        == [sha256(root / "resolved.json")] * 2
    )
    assert source["inputs_sha256"] == inspection["inputs"]
    assert receipt["source_resolution"]["ordered_sources"] == len(source["ordered_sources"])
    assert receipt["source_resolution"]["generated_sources"] == len(
        source["generated_sources_sha256"]
    )


def test_opentitan_placement_figure_is_bound_to_a_physical_checkpoint() -> None:
    root = ROOT / "docs/evidence/opentitan-aes"
    physical = json.loads((root / "physical.json").read_bytes())
    inspection = json.loads((root / "inspect.json").read_bytes())
    figure = physical["placement_figure"]
    checkpoint = figure["provenance"]["source"]
    assert figure["provenance"]["run_id"] == physical["run_id"]
    assert checkpoint["stage"] == "place"
    assert checkpoint["sha256"] == physical["artifact_sha256"][checkpoint["path"]]
    assert (
        inspection["conversion"]["output_sha256"] == physical["artifact_sha256"]["aes-input/aes.v"]
    )
    assert figure["sha256"] == sha256(
        ROOT / "docs/images/examples/opentitan-aes" / figure["filename"]
    )
