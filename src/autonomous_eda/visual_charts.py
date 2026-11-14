"""Agg charts from existing diagnosis and optimization schemas."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .visual_theme import (
    BLUE,
    DPI,
    FIGURE_INCHES,
    GRAY,
    INK,
    PAPER,
    RC,
    RED,
    STATUS_COLORS,
    TABLE_BORDER,
    TABLE_HEADER,
    TABLE_ROW,
)


def chart(path: Path, title: str, detail: str, draw: Callable[[Any], None]) -> str:
    mpl = importlib.import_module("matplotlib")
    mpl.use("Agg")
    plt = importlib.import_module("matplotlib.pyplot")
    with plt.rc_context(RC):
        fig, ax = plt.subplots(figsize=FIGURE_INCHES, layout="constrained")
        fig.suptitle(title, fontsize=20, fontweight="semibold", x=0.05, ha="left")
        ax.set_title(detail, fontsize=10, loc="left", color=GRAY, pad=12, wrap=True)
        try:
            draw(ax)
            fig.savefig(path, dpi=DPI, metadata={"Software": f"Matplotlib {mpl.__version__}"})
        finally:
            plt.close(fig)
    return str(mpl.__version__)


def bars(ax: Any, labels: list[str], values: list[float | None], unit: str) -> None:
    ax.barh(
        labels,
        [value or 0 for value in values],
        color=[GRAY if value is None else RED if value < 0 else BLUE for value in values],
    )
    ax.invert_yaxis()
    ax.axvline(0, color=INK, linewidth=1)
    ax.set_xlabel(unit)
    ax.grid(axis="x", alpha=0.2)
    for i, value in enumerate(values):
        ax.annotate(
            "UNKNOWN" if value is None else f"{value:.6g}",
            (value or 0, i),
            xytext=(6 if value is None or value >= 0 else -6, 0),
            textcoords="offset points",
            ha="left" if value is None or value >= 0 else "right",
            va="center",
            fontsize=10,
        )
    ax.margins(x=0.25)


def table(
    ax: Any, columns: list[str], rows: list[list[str]], *, widths: list[float] | None = None
) -> None:
    ax.set_axis_off()
    if widths is None and len(columns) == 2:
        widths = [0.34, 0.66]
    grid = ax.table(
        cellText=rows,
        colLabels=columns,
        colWidths=widths,
        loc="upper center",
        cellLoc="left",
        colLoc="left",
    )
    grid.auto_set_font_size(False)
    grid.set_fontsize(11)
    grid.scale(1, min(1.8, 27 / (len(rows) + 1)))
    for (row, _column), cell in grid.get_celld().items():
        cell.set_edgecolor(TABLE_BORDER)
        cell.set_facecolor(TABLE_HEADER if row == 0 else PAPER if row % 2 else TABLE_ROW)
        if row == 0:
            cell.get_text().set_fontweight("bold")
        text = cell.get_text().get_text()
        if text in STATUS_COLORS:
            cell.get_text().set_color(STATUS_COLORS[text])
