"""Locate printed table cells in chart rasters, never infer values from bars.

The geometry is evidence, not an admission decision. Unknown labels, missing
grid lines and unreadable tokens remain explicit gaps for the caller.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from .factset_report_layout import Word, ocr_words, visual_lines


@dataclass(frozen=True)
class GridCell:
    row: int
    column: int
    region: tuple[float, float, float, float]
    text: str


@dataclass(frozen=True)
class PrintedGrid:
    region: tuple[float, float, float, float]
    cells: tuple[GridCell, ...]
    row_count: int
    column_count: int


def _centres(indices: list[int]) -> list[int]:
    runs: list[list[int]] = []
    for value in indices:
        if runs and value == runs[-1][-1] + 1:
            runs[-1].append(value)
        else:
            runs.append([value])
    return [round(sum(run) / len(run)) for run in runs]


def cell_words(words: tuple[Word, ...], region: tuple[float, float, float, float]) -> str:
    """Containment, not nearest-neighbour assignment across a table border."""
    x0, y0, x1, y1 = region
    inside = [word for word in words if x0 <= word.box[0] < word.box[2] <= x1
              and y0 <= word.box[1] < word.box[3] <= y1]
    return ' '.join(line for line, _ in visual_lines(tuple(inside)))


def locate_printed_grids(data: bytes, *, words: tuple[Word, ...] = (),
                         min_columns: int = 3) -> tuple[PrintedGrid, ...]:
    """Find consecutive ruled rows with common column boundaries.

    Neutral long horizontal lines and intersecting vertical lines locate a
    printed data table. Bar colours/heights, report dates, image ordinals and
    sector ordering play no part. A broken grid yields no guessed cells.
    """
    from PIL import Image

    if min_columns < 2:
        raise ValueError('min_columns must be at least 2')
    with Image.open(BytesIO(data)) as original:
        raster = original.convert('RGB')
        width, height = raster.size
        pixels = list(raster.get_flattened_data() if hasattr(raster, 'get_flattened_data') else raster.getdata())
    neutral = bytearray(max(rgb) - min(rgb) < 15 and sum(rgb) < 600 for rgb in pixels)
    horizontal = _centres([y for y in range(height)
                           if sum(neutral[y * width:(y + 1) * width]) > width * .65])
    bands = []
    for top, bottom in zip(horizontal, horizontal[1:]):
        if bottom - top < 4:
            continue
        columns = _centres([x for x in range(width)
                            if sum(neutral[y * width + x] for y in range(top + 1, bottom))
                            >= (bottom - top - 1) * .9])
        if len(columns) >= min_columns + 1:
            bands.append((top, bottom, columns))
    # Extra vertical marks inside a legend cell must not split sector columns.
    # Use only boundaries that continue across every row of this table.
    groups: list[list[tuple]] = []
    for band in bands:
        if groups and groups[-1][-1][1] == band[0]:
            groups[-1].append(band)
        else:
            groups.append([band])
    grids = []
    for group in groups:
        if len(group) < 2:
            continue
        common = set(group[0][2])
        for band in group[1:]:
            common.intersection_update(band[2])
        columns = sorted(common)
        if len(columns) < min_columns + 1:
            continue
        # The outer frame can run down the entire image. Discard skinny
        # frame margins relative to the median width, not fixed pixel crops.
        widths = sorted(b - a for a, b in zip(columns, columns[1:]))
        typical = widths[len(widths) // 2]
        while len(columns) > 2 and columns[1] - columns[0] < typical * .25:
            columns.pop(0)
        while len(columns) > 2 and columns[-1] - columns[-2] < typical * .25:
            columns.pop()
        cells = []
        for row, (top, bottom, _) in enumerate(group):
            for col, (left, right) in enumerate(zip(columns, columns[1:])):
                region = ((left + 1) / width, (top + 1) / height,
                          right / width, bottom / height)
                cells.append(GridCell(row, col, region, cell_words(words, region)))
        grids.append(PrintedGrid((columns[0] / width, group[0][0] / height,
                                  columns[-1] / width, group[-1][1] / height),
                                 tuple(cells), len(group), len(columns) - 1))
    return tuple(grids)


def read_grid_cell(data: bytes, region: tuple[float, float, float, float],
                   *, reader=ocr_words, timeout: int = 30, scale: int = 4) -> str:
    """OCR a dynamically located cell, keeping the exact source token.

    No numeric whitelist (which can erase a minus sign), punctuation repair,
    missing-value zero fill or external model fallback.
    """
    from PIL import Image, ImageOps

    with Image.open(BytesIO(data)) as original:
        width, height = original.size
        x0, y0, x1, y1 = region
        if not 0 <= x0 < x1 <= 1 or not 0 <= y0 < y1 <= 1:
            raise ValueError('invalid cell region')
        crop = original.crop((round(x0 * width), round(y0 * height),
                              round(x1 * width), round(y1 * height))).convert('RGB')
        crop = ImageOps.expand(crop, border=4, fill='white')
        output = BytesIO()
        crop.save(output, format='PNG')
    words = reader(output.getvalue(), scale=scale, timeout=timeout, psm=6)
    return ' '.join(line for line, _ in visual_lines(words))
