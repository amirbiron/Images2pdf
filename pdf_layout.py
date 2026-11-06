"""
pdf_layout.py — פריסת התמונות בעמודי A4. חישוב בלבד: בלי Pillow, בלי ReportLab ובלי קבצים.

הכללים (ROADMAP, סעיף "הפריסה"):
1. שוליים 12 מ"מ, רווח 5 מ"מ בין תמונות, וכותרת של 22 מ"מ בעמוד הראשון בלבד.
2. שתי עמודות ברוחב שווה: (רוחב העמוד - 2×שוליים - רווח) / 2. גובה תמונה = רוחב עמודה / יחס.
3. עד 4 תמונות בעמוד, לפי סדר הבחירה. כל תמונה נכנסת לעמודה הקצרה יותר; בתיקו — לימנית.
4. אם העמודה הגבוהה חורגת מהגובה הפנוי, מקטינים את כל התמונות והרווחים של העמוד באותו יחס.
5. העמודות מיושרות לראש העמוד וממורכזות לרוחב. תמונה בודדת בעמוד ממורכזת לרוחב בעצמה,
   באותו גודל של תמונה בעמודה (החלטה אחרי שלב 1: בלי זה חצי העמוד השמאלי נשאר ריק).
6. כיוון העמוד אחיד בכל הקובץ: מחשבים לאורך ולרוחב, ובוחרים את מה שבו שטח התמונות
   הכולל גדול יותר. בתיקו — לאורך.

כל המידות במילימטרים. y נמדד מראש העמוד כלפי מטה (ההמרה לנקודות PDF, שבהן y עולה
מלמטה, נעשית ב-pdf_builder).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

A4_SHORT_MM = 210.0
A4_LONG_MM = 297.0
MARGIN_MM = 12.0
GAP_MM = 5.0
HEADER_MM = 22.0
IMAGES_PER_PAGE = 4

PORTRAIT = "portrait"
LANDSCAPE = "landscape"

# בתיקו בוחרים לאורך. שני חישובים של אותו שטח יכולים להיבדל בשארית של נקודה צפה,
# ולכן "גדול יותר" פירושו גדול יותר ממילימטר רבוע אחד מתוך עשרות אלפים.
_AREA_TIE_MM2 = 1.0


@dataclass(frozen=True)
class Placement:
    index: int  # המיקום ברשימה המקורית, כלומר בסדר הבחירה
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class Page:
    placements: tuple[Placement, ...]
    has_header: bool
    scale: float
    # גבולות גוש העמודות (גם כשעמודה אחת ריקה), כדי ליישר אליהם את הכותרת והלוגו.
    block_x: float
    block_width: float


@dataclass(frozen=True)
class DocumentLayout:
    orientation: str
    page_width: float
    page_height: float
    pages: tuple[Page, ...]
    image_area: float  # סכום שטחי התמונות בכל העמודים, מ"מ רבועים


def _validate(aspects: Sequence[float]) -> None:
    """כל יחס הוא מספר סופי וחיובי. bool נדחה במפורש: ב-Python הוא גם int."""
    if not aspects:
        raise ValueError("at least one image is required")
    for a in aspects:
        if isinstance(a, bool) or not isinstance(a, (int, float)) or not math.isfinite(a) or a <= 0:
            raise ValueError("every aspect ratio must be a finite positive number")


def layout_page(
    aspects: Sequence[float],
    page_width: float,
    page_height: float,
    *,
    has_header: bool,
    first_index: int = 0,
) -> Page:
    """פריסה של עמוד אחד (עד 4 תמונות). `aspects` הם רוחב/גובה של כל תמונה."""
    if len(aspects) > IMAGES_PER_PAGE:
        raise ValueError("too many images for one page")
    _validate(aspects)

    col_w = (page_width - 2 * MARGIN_MM - GAP_MM) / 2
    top = MARGIN_MM + (HEADER_MM if has_header else 0.0)
    available_h = page_height - MARGIN_MM - top

    # עמודה 0 היא הימנית, עמודה 1 השמאלית. y יחסי לראש הגוש, לפני הקטנה.
    heights = [0.0, 0.0]
    raw: list[tuple[int, int, float, float]] = []  # (index, column, y, h)
    for offset, aspect in enumerate(aspects):
        h = col_w / aspect
        column = 0 if heights[0] <= heights[1] else 1
        y = heights[column] + (GAP_MM if heights[column] > 0 else 0.0)
        raw.append((first_index + offset, column, y, h))
        heights[column] = y + h

    tallest = max(heights)
    scale = min(1.0, available_h / tallest)
    block_width = (2 * col_w + GAP_MM) * scale
    block_x = (page_width - block_width) / 2
    column_x = (block_x + (col_w + GAP_MM) * scale, block_x)  # ימנית, שמאלית
    if len(raw) == 1:
        # תמונה בודדת: אותו רוחב של עמודה, במרכז העמוד ולא בעמודה הימנית.
        single_x = (page_width - col_w * scale) / 2
        column_x = (single_x, single_x)

    placements = tuple(
        Placement(
            index=index,
            x=column_x[column],
            y=top + y * scale,
            width=col_w * scale,
            height=h * scale,
        )
        for index, column, y, h in raw
    )
    return Page(placements=placements, has_header=has_header, scale=scale, block_x=block_x, block_width=block_width)


def _layout_in(aspects: Sequence[float], orientation: str, *, header: bool) -> DocumentLayout:
    """פריסת כל העמודים בכיוון אחד, ארבע תמונות לעמוד לפי הסדר, וסכום שטח התמונות."""
    if orientation == PORTRAIT:
        width, height = A4_SHORT_MM, A4_LONG_MM
    else:
        width, height = A4_LONG_MM, A4_SHORT_MM
    pages = []
    for start in range(0, len(aspects), IMAGES_PER_PAGE):
        pages.append(
            layout_page(
                aspects[start : start + IMAGES_PER_PAGE],
                width,
                height,
                has_header=header and start == 0,
                first_index=start,
            )
        )
    area = sum(p.width * p.height for page in pages for p in page.placements)
    return DocumentLayout(orientation=orientation, page_width=width, page_height=height, pages=tuple(pages), image_area=area)


def layout_document(aspects: Sequence[float], *, header: bool = True) -> DocumentLayout:
    """פריסת כל הקובץ, בכיוון שבו שטח התמונות הכולל גדול יותר (בתיקו — לאורך)."""
    _validate(aspects)
    portrait = _layout_in(aspects, PORTRAIT, header=header)
    landscape = _layout_in(aspects, LANDSCAPE, header=header)
    if landscape.image_area > portrait.image_area + _AREA_TIE_MM2:
        return landscape
    return portrait
