"""Plain-text table output.

Deliberately dependency-free: two spaces between columns, a dashed rule under
the header, and an ellipsis when a cell is too wide. Everything is truncated on
a character boundary, so a table never wraps and never needs a terminal width
library.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

ELLIPSIS = "\u2026"


def truncate(value: object, width: int) -> str:
    """Collapse whitespace and cut to ``width`` characters, with an ellipsis."""
    text = " ".join(str(value if value is not None else "").split())
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return ELLIPSIS
    return text[: width - 1] + ELLIPSIS


def render_table(
    headers: Sequence[str],
    rows: Iterable[Sequence[object]],
    widths: Optional[Sequence[int]] = None,
    indent: str = "",
) -> str:
    """Render an aligned table.

    ``widths`` caps each column; without it columns size to their content,
    capped at 60 characters to keep the output readable.
    """
    header_cells = [str(h) for h in headers]
    body: List[List[str]] = []
    for row in rows:
        cells = ["" if cell is None else str(cell) for cell in row]
        if len(cells) != len(header_cells):
            raise ValueError(
                "row has {} cells, expected {}".format(len(cells), len(header_cells))
            )
        body.append(cells)

    if widths is None:
        computed = []
        for index, header in enumerate(header_cells):
            longest = max(
                [len(header)] + [len(row[index]) for row in body] if body else [len(header)]
            )
            computed.append(min(60, max(4, longest)))
        widths = computed

    widths = list(widths)
    if len(widths) != len(header_cells):
        raise ValueError("widths must match the number of columns")

    def line(cells: Sequence[str]) -> str:
        return indent + "  ".join(
            truncate(cell, widths[i]).ljust(widths[i])
            for i, cell in enumerate(cells)
        ).rstrip()

    output = [line(header_cells)]
    output.append(indent + "  ".join("-" * width for width in widths))
    for cells in body:
        output.append(line(cells))
    return "\n".join(output)
