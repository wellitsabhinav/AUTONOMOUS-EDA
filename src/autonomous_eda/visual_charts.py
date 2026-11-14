"""Agg charts from existing diagnosis and optimization schemas."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

INK = "#14253b"
BLUE = "#245a9e"
RED = "#b43838"
GREEN = "#257059"
GRAY = "#65758a"


def chart(path: Path, title: str, detail: str, draw: Callable[[Any], None]) -> str:
    mpl = importlib.import_module("matplotlib")
    mpl.use("Agg")
    plt = importlib.import_module("matplotlib.pyplot")
    with plt.rc_context(
        {
            "font.family": "DejaVu Sans",
            "font.size": 12,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.labelcolor": INK,
            "text.color": INK,
            "axes.titlecolor": INK,
            "figure.facecolor": "#ffffff",
            "axes.facecolor": "#ffffff",
        }
    ):
        fig, ax = plt.subplots(figsize=(10, 6.2), layout="constrained")
        fig.suptitle(title, fontsize=21, fontweight="bold", x=0.05, ha="left")
        ax.set_title(detail, fontsize=11, loc="left", color=GRAY, pad=12, wrap=True)
        try:
            draw(ax)
            fig.savefig(path, dpi=170, metadata={"Software": f"Matplotlib {mpl.__version__}"})
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
            "Unavailable" if value is None else f"{value:.6g}",
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
        cell.set_edgecolor("#d6dee8")
        cell.set_facecolor("#e4eefb" if row == 0 else "#ffffff" if row % 2 else "#f3f6fa")
        if row == 0:
            cell.get_text().set_fontweight("bold")
        text = cell.get_text().get_text()
        if text in {"FAILED", "INFEASIBLE", "CANCELLED"}:
            cell.get_text().set_color(RED)
        if text in {"PASSED", "FEASIBLE"}:
            cell.get_text().set_color(GREEN)
