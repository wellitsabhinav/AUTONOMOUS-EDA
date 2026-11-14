"""Shared presentation settings for generated engineering charts."""

from typing import Any

INK = "#172a40"
BLUE = "#245a9e"
RED = "#a43131"
GREEN = "#20634e"
AMBER = "#805500"
GRAY = "#56667a"
PAPER = "#ffffff"
TABLE_HEADER = "#e4eefb"
TABLE_ROW = "#f4f7fb"
TABLE_BORDER = "#d6dee8"
DPI = 170
FIGURE_INCHES = (10, 6.2)
FONT = "DejaVu Sans"
THEME_VERSION = 1

STATUS_COLORS = {
    "PASS": GREEN,
    "FAIL": RED,
    "SKIPPED": GRAY,
    "PASSED": GREEN,
    "FEASIBLE": GREEN,
    "FAILED": RED,
    "INFEASIBLE": RED,
    "CANCELLED": RED,
    "BLOCKED": AMBER,
    "UNKNOWN": GRAY,
    "NOT_APPLICABLE": GRAY,
    "UNVERIFIED": AMBER,
}

RC: dict[str, Any] = {
    "font.family": FONT,
    "font.size": 11,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.labelcolor": INK,
    "axes.titlecolor": INK,
    "axes.edgecolor": GRAY,
    "axes.linewidth": 0.8,
    "text.color": INK,
    "figure.facecolor": PAPER,
    "axes.facecolor": PAPER,
    "legend.frameon": False,
    "lines.linewidth": 1.5,
    "axes.formatter.useoffset": False,
}


def settings(title: str, detail: str) -> dict[str, object]:
    return {
        "theme_version": THEME_VERSION,
        "dpi": DPI,
        "figure_inches": list(FIGURE_INCHES),
        "typography": FONT,
        "title": title,
        "detail": detail,
    }
