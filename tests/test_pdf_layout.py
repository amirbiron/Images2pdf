"""הפריסה: ערכי הטבלה ב-ROADMAP, בחירת כיוון לפי שטח, מספרי תמונות שונים ויחסים קיצוניים."""

from __future__ import annotations

import itertools
import math

import pytest

import pdf_layout
from pdf_layout import (
    GAP_MM,
    HEADER_MM,
    LANDSCAPE,
    MARGIN_MM,
    PORTRAIT,
    layout_document,
    layout_page,
)


@pytest.mark.parametrize(
    "aspect, orientation, without_header, with_header",
    [
        (3 / 2, LANDSCAPE, (134, 89), (120, 80)),
        (2 / 3, PORTRAIT, (89, 134), (82, 123)),
        (9 / 16, PORTRAIT, (76, 134), (70, 124)),
    ],
)
def test_roadmap_table(aspect, orientation, without_header, with_header):
    """ROADMAP, טבלת "ערכים לבדיקות יחידה": 4 תמונות זהות, כותרת של 22 מ"מ בדיוק."""
    for header, expected in ((False, without_header), (True, with_header)):
        doc = layout_document([aspect] * 4, header=header)
        assert doc.orientation == orientation
        for p in doc.pages[0].placements:
            assert (round(p.width), round(p.height)) == expected


def test_orientation_by_area_not_by_majority():
    """הדוגמה מה-ROADMAP: שתי 3:2 ושתי 2:3 — 57% מעמוד לאורך מול 28% לרוחב."""
    aspects = [3 / 2, 2 / 3, 3 / 2, 2 / 3]
    portrait = layout_page(aspects, 210, 297, has_header=False)
    landscape = layout_page(aspects, 297, 210, has_header=False)
    area = lambda page: sum(p.width * p.height for p in page.placements) / (210 * 297)  # noqa: E731
    assert round(area(portrait) * 100) == 57
    assert round(area(landscape) * 100) == 28
    assert layout_document(aspects, header=False).orientation == PORTRAIT


def test_area_beats_majority():
    """שלוש תמונות 16:9 ואחת 3:4: רוב "לרוחב", אבל השטח הכולל גדול יותר לאורך.

    בדוגמה של ה-ROADMAP (2 מול 2) אין רוב בכלל, ולכן היא לבדה לא מבדילה בין הכללים.
    """
    aspects = [16 / 9, 16 / 9, 16 / 9, 3 / 4]
    portrait = pdf_layout._layout_in(aspects, PORTRAIT, header=True)
    landscape = pdf_layout._layout_in(aspects, LANDSCAPE, header=True)
    assert portrait.image_area > landscape.image_area
    assert layout_document(aspects).orientation == PORTRAIT


def test_tie_goes_to_portrait(monkeypatch):
    real = pdf_layout._layout_in

    def same_area(aspects, orientation, *, header):
        doc = real(aspects, orientation, header=header)
        return pdf_layout.DocumentLayout(doc.orientation, doc.page_width, doc.page_height, doc.pages, 1000.0)

    monkeypatch.setattr(pdf_layout, "_layout_in", same_area)
    assert layout_document([1.0]).orientation == PORTRAIT


def _check_document(doc, aspects, *, header=True):
    """בדיקות שחלות על כל פריסה: סדר, גבולות, יחס, חפיפה, יישור."""
    indices = [p.index for page in doc.pages for p in page.placements]
    assert indices == list(range(len(aspects))), "התמונות נשמרות בסדר הבחירה"
    assert len(doc.pages) == math.ceil(len(aspects) / 4)
    for n, page in enumerate(doc.pages):
        assert page.has_header == (header and n == 0), "כותרת בעמוד הראשון בלבד"
        assert 1 <= len(page.placements) <= 4
        top = MARGIN_MM + (HEADER_MM if page.has_header else 0)
        # מרוכז לרוחב
        assert math.isclose(page.block_x * 2 + page.block_width, doc.page_width, abs_tol=1e-6)
        for p in page.placements:
            # בלי חיתוך ובלי מתיחה: היחס נשמר
            assert math.isclose(p.width / p.height, aspects[p.index], rel_tol=1e-9)
            # בתוך השוליים
            assert p.x >= MARGIN_MM - 1e-6 and p.x + p.width <= doc.page_width - MARGIN_MM + 1e-6
            assert p.y >= top - 1e-6 and p.y + p.height <= doc.page_height - MARGIN_MM + 1e-6
        # בלי חפיפה, ועם רווח מוקטן באותו יחס לפחות בין תמונות באותה עמודה
        for a, b in itertools.combinations(page.placements, 2):
            overlap_x = min(a.x + a.width, b.x + b.width) - max(a.x, b.x)
            overlap_y = min(a.y + a.height, b.y + b.height) - max(a.y, b.y)
            assert overlap_x <= 1e-6 or overlap_y <= 1e-6
            if math.isclose(a.x, b.x):
                assert overlap_y <= -(GAP_MM * page.scale) + 1e-6
        # מיושר לראש העמוד: התמונה הראשונה בכל עמודה מתחילה בראש
        for x in {round(p.x, 6) for p in page.placements}:
            column = [p for p in page.placements if round(p.x, 6) == x]
            assert math.isclose(min(p.y for p in column), top, abs_tol=1e-6)


@pytest.mark.parametrize("count", [1, 3, 4, 5, 8, 9, 12])
def test_image_counts_and_page_breaks(count):
    aspects = [(3 / 2, 2 / 3, 1.0, 4 / 3, 9 / 16)[i % 5] for i in range(count)]
    doc = layout_document(aspects)
    _check_document(doc, aspects)
    assert [len(p.placements) for p in doc.pages] == [min(4, count - 4 * i) for i in range(math.ceil(count / 4))]


def test_first_image_goes_right_and_ties_go_right():
    page = layout_page([1.0, 1.0, 1.0], 210, 297, has_header=False)
    right_x = page.block_x + page.block_width - page.placements[0].width
    assert math.isclose(page.placements[0].x, right_x)  # הראשונה: ימין (תיקו 0=0)
    assert page.placements[1].x < page.placements[0].x  # השנייה: לעמודה הקצרה, שמאל
    assert math.isclose(page.placements[2].x, right_x)  # השלישית: תיקו שוב → ימין


def test_image_goes_to_shorter_column():
    # 1: ימין (גבוהה, 9:16). 2: שמאל. 3: שמאל, כי השמאלית (פנורמה נמוכה) עדיין קצרה יותר.
    page = layout_page([9 / 16, 3.0, 3.0], 210, 297, has_header=False)
    assert page.placements[1].x == page.placements[2].x < page.placements[0].x


def test_extreme_ratios_tall_next_to_panorama():
    aspects = [9 / 16, 3.0, 9 / 16, 3.0]
    for header in (True, False):
        doc = layout_document(aspects, header=header)
        _check_document(doc, aspects, header=header)
    # עמוד שבו 9:16 ענקית ליד פנורמה: מוקטן כדי להיכנס, והפנורמה נשארת 3:1
    page = layout_page([9 / 16, 9 / 16, 9 / 16, 3.0], 210, 297, has_header=True)
    assert page.scale < 1
    pano = page.placements[3]
    assert math.isclose(pano.width / pano.height, 3.0)


@pytest.mark.parametrize("bad", [0, -1.5, float("nan"), float("inf"), True, "1.5", None])
def test_invalid_aspects_are_rejected(bad):
    with pytest.raises(ValueError):
        layout_document([1.0, bad])


def test_empty_is_rejected():
    with pytest.raises(ValueError):
        layout_document([])
