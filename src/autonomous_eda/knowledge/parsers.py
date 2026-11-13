"""Line-preserving parsers for local engineering text."""

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

PARSER_VERSION = 1
MAX_CHARS = 8192
MAX_CHUNKS = 4096
MAX_LINES = 100_000
TYPES = {"markdown", "text", "reference", "config", "timing", "synthesis", "log", "drc", "lvs"}
DIAGNOSTIC = re.compile(
    r"^\s*\[?(WARNING|WARN|ERROR|FATAL)(?:\s+([A-Z][A-Z0-9]+-\d+))?(?:\]|\b)", re.I
)
TOOL = re.compile(r"^\s*(?:OpenROAD|OpenSTA|Yosys|KLayout)\s+v?(\S+)", re.I)
COMMAND = re.compile(r"^\s*(?:command:\s*|executing:\s*|\$\s+)(.+)", re.I)
FIELD = re.compile(r"^\s*(Startpoint|Endpoint|Path Group|Path Type|Corner|Clock):\s*(.+)", re.I)
VALUE = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s+"
    r"(data arrival time|data required time|slack)\b",
    re.I,
)


@dataclass(frozen=True)
class Excerpt:
    start: int
    end: int
    text: str
    heading: str = ""
    fields: dict[str, str] = field(default_factory=dict)


def line_excerpt(
    lines: list[str], start: int, end: int, heading: str = "", fields: dict[str, str] | None = None
) -> Excerpt:
    return Excerpt(start + 1, end, "\n".join(lines[start:end]), heading, fields or {})


def markdown_sections(lines: list[str]) -> Iterator[tuple[int, str]]:
    headings: list[tuple[int, str]] = []
    fence = ""
    for index, line in enumerate(lines):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if not fence:
                fence = token
            elif token.startswith(fence) and not line.lstrip()[len(token) :].strip():
                fence = ""
            continue
        if fence:
            continue
        atx = re.match(r"^ {0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$", line)
        if atx:
            level, title = len(atx[1]), atx[2]
        elif (
            index
            and re.fullmatch(r" {0,3}(=+|-+)\s*", line)
            and lines[index - 1].strip()
            and not lines[index - 1].lstrip().startswith("#")
        ):
            level, title = (1 if "=" in line else 2), lines[index - 1].strip()
            index -= 1
        else:
            continue
        headings = [(depth, name) for depth, name in headings if depth < level]
        headings.append((level, title))
        yield index, " / ".join(name for _, name in headings)[:512]


def sections(lines: list[str], kind: str) -> Iterator[tuple[int, str]]:
    if kind in {"markdown", "reference"}:
        yield from markdown_sections(lines)
        if kind == "reference":
            for index, line in enumerate(lines):
                if re.fullmatch(r"[A-Z][A-Z0-9_]+:\s*", line):
                    yield index, line.strip().rstrip(":")
    elif kind == "timing":
        for index, line in enumerate(lines):
            if re.match(r"^\s*Startpoint:", line, re.I):
                yield index, "Timing path"
            elif index and lines[index - 1].startswith("=====") and line.strip():
                yield index, line.strip()[:512]
    elif kind in {"config", "synthesis"}:
        for index, line in enumerate(lines):
            if re.match(r"^\s*(?:\[[\w.]+\]|=== .+ ===|\d+\.\s+Executing\b)", line):
                yield index, line.strip()[:512]


def groups(lines: list[str], breaks: Iterable[tuple[int, str]]) -> Iterator[tuple[int, int, str]]:
    start, heading = 0, ""
    for index, name in breaks:
        if index > start:
            yield start, index, heading
        start, heading = index, name
    yield start, len(lines), heading


def bounded(lines: list[str], start: int, end: int) -> Iterator[tuple[int, int]]:
    while start < end:
        stop, size, paragraph = start, 0, start
        while stop < end and size + len(lines[stop]) + (stop > start) <= MAX_CHARS:
            size += len(lines[stop]) + (stop > start)
            stop += 1
            if not lines[stop - 1].strip():
                paragraph = stop
        if stop < end and paragraph > start and paragraph - start >= (stop - start) // 2:
            stop = paragraph
        yield start, stop
        start = stop


def timing_fields(lines: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    arrivals = []
    for line in lines:
        if match := FIELD.match(line):
            key = match[1].lower().replace(" ", "_")
            fields.setdefault(key, match[2].strip())
        if match := VALUE.match(line):
            key = match[2].lower().replace(" ", "_").removeprefix("data_")
            if key == "arrival_time":
                arrivals.append(match[1])
            else:
                fields.setdefault(key, match[1])
    # A trailing slack equation may negate the arrival term. When the
    # excerpt lacks the path header, its isolated term has ambiguous meaning.
    if arrivals and "startpoint" in fields:
        fields["arrival_time"] = arrivals[0]
    # OpenSTA report_checks names setup and hold paths max and min respectively.
    path_type = fields.get("path_type", "").casefold()
    check = {"min": "hold", "max": "setup", "setup": "setup", "hold": "hold"}.get(path_type)
    if check:
        fields["check"] = check
    return fields


def diagnostics(lines: list[str]) -> Iterator[Excerpt]:
    matches = [index for index, line in enumerate(lines) if DIAGNOSTIC.match(line)]
    version = next((m[1] for line in lines[:50] if (m := TOOL.match(line))), None)
    for position, index in enumerate(matches):
        match = DIAGNOSTIC.match(lines[index])
        assert match is not None
        previous = matches[position - 1] + 1 if position else 0
        following = matches[position + 1] if position + 1 < len(matches) else len(lines)
        start, end = max(previous, index - 2), min(following, index + 4)
        while end < following and lines[end].startswith((" ", "\t")) and lines[end].strip():
            end += 1
        fields = {"severity": match[1].upper()}
        if match[2]:
            fields["diagnostic"] = match[2]
        if version and version != "unknown":
            fields["tool_version"] = version
        for line in reversed(lines[max(0, index - 12) : index]):
            if command := COMMAND.match(line):
                fields["command"] = command[1]
                break
        if sum(len(line) + 1 for line in lines[start:end]) - 1 > MAX_CHARS:
            start, end = index, index + 1
            size = len(lines[index])
            while end < following and lines[end].strip():
                size += len(lines[end]) + 1
                if size > MAX_CHARS:
                    break
                end += 1
        yield line_excerpt(lines, start, end, fields=fields)


def parse(text: str, kind: str) -> list[Excerpt]:
    if kind not in TYPES:
        raise ValueError("Unsupported engineering document type")
    lines = text.split("\n")
    if len(lines) > MAX_LINES or any(len(line) > MAX_CHARS for line in lines):
        raise ValueError("Source exceeds line count or line length limit")
    if kind in {"log", "synthesis", "drc", "lvs"}:
        excerpts = list(diagnostics(lines))
    else:
        excerpts = []
    for start, end, heading in groups(lines, sorted(sections(lines, kind))):
        fields = timing_fields(lines[start:end]) if kind == "timing" else {}
        for first, last in bounded(lines, start, end):
            while first < last and not lines[first].strip():
                first += 1
            while first < last and not lines[last - 1].strip():
                last -= 1
            if first < last:
                # Only the slice containing the extracted field may cite it.
                located = (
                    fields
                    if (first, last) == (start, end)
                    else (timing_fields(lines[first:last]) if fields else {})
                )
                label = heading
                if check := located.get("check"):
                    label = f"{check.title()} timing path"
                if slack := located.get("slack"):
                    label += f" slack {slack}"
                    if any("slack (VIOLATED)" in line for line in lines[first:last]):
                        label += " violated"
                excerpts.append(line_excerpt(lines, first, last, label, located))
    if len(excerpts) > MAX_CHUNKS:
        raise ValueError("Source exceeds excerpt count limit")
    return sorted(excerpts, key=lambda excerpt: (excerpt.start, excerpt.end))
