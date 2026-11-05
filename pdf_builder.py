"""
pdf_builder.py — כתיבת קובץ ה-PDF מתמונות מעובדות ומפריסה מוכנה.

ReportLab עם canvas.Canvas (ולא WeasyPrint, בגלל מגבלת הזיכרון). התמונות מצוירות
מהקבצים הזמניים: ל-JPEG עם סיומת .jpg ReportLab מטמיע את הבתים כמו שהם, בלי פענוח.
מקור: reportlab/pdfbase/pdfdoc.py, PDFImageXObject.loadImageFromJPEG.

הכותרת (בעמוד הראשון בלבד): שם הלוקיישן, מיושר לימין גוש התמונות, עם קו תחתון עדין
שמסמן שהיא לחיצה וקישור לתיקייה בדרייב. הלוגו בצד שמאל. ReportLab מצייר טקסט משמאל
לימין כמו שהוא, ולכן הטקסט עובר קודם דרך האלגוריתם הדו-כיווני של python-bidi.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from bidi import get_display
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from config import is_valid_drive_id
from image_processing import ProcessedImage
from pdf_layout import MARGIN_MM, DocumentLayout, Page

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
FONT_PATH = ROOT / "assets" / "fonts" / "Heebo-Bold.ttf"
FONT_NAME = "Heebo-Bold"
LOGO_PATH = ROOT / "static" / "logo.png"

TITLE_MAX_CHARS = 120
TITLE_MAX_PT = 20.0
HEADER_BAND_MM = 16.0  # מתוך 22 המ"מ של הכותרת; השאר הוא רווח עד התמונות
LOGO_HEIGHT_MM = 14.0
LOGO_MAX_WIDTH_MM = 45.0
TITLE_LOGO_GAP_MM = 6.0
TITLE_COLOR = (0.13, 0.13, 0.13)
UNDERLINE_COLOR = (0.62, 0.62, 0.62)
CREATOR = "כלי PDF ללוקיישנים"

_ISRAEL = ZoneInfo("Asia/Jerusalem")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
# תווים שאסורים בשם קובץ ב-Windows/macOS/Linux, ותווי כיווניות שמאפשרים להסוות סיומת.
_FILENAME_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f\u200e\u200f\u202a-\u202e\u2066-\u2069]')

_font_lock = threading.Lock()
_font_glyphs: frozenset[int] | None = None  # מתפרסם רק אחרי רישום מוצלח


@dataclass(frozen=True)
class BuildResult:
    pages: int
    title_font_size: float
    removed_characters: str  # תווים שאין להם צורה בפונט, והוסרו מהכותרת


def _ensure_font() -> frozenset[int]:
    """רושם את הפונט פעם אחת. השומר (`_font_glyphs`) מוצב רק אחרי שהרישום הצליח,
    כך שכשל לא משאיר מצב שבו קריאה הבאה מדלגת על הרישום."""
    global _font_glyphs
    if _font_glyphs is not None:
        return _font_glyphs
    with _font_lock:
        if _font_glyphs is None:
            font = TTFont(FONT_NAME, str(FONT_PATH))
            pdfmetrics.registerFont(font)
            _font_glyphs = frozenset(font.face.charToGlyph.keys())
    return _font_glyphs


def folder_url(folder_id: str) -> str:
    if not is_valid_drive_id(folder_id):
        raise ValueError("invalid folder id")
    return f"https://drive.google.com/drive/folders/{folder_id}"


def clean_title(title: object) -> str:
    """כותרת תקינה: מחרוזת, בלי תווי בקרה, לא ריקה, עד TITLE_MAX_CHARS תווים.

    כותרת ארוכה מדי נדחית ולא נחתכת: כותרת חתוכה היא שם לוקיישן אחר.
    """
    if not isinstance(title, str):
        raise ValueError("title must be a string")
    cleaned = " ".join(_CONTROL_CHARS.sub(" ", title).split())
    if not cleaned:
        raise ValueError("title is empty")
    if len(cleaned) > TITLE_MAX_CHARS:
        raise ValueError("title is too long")
    return cleaned


def pdf_filename(title: str, day: date | None = None) -> str:
    """`<כותרת> <DD-MM-YYYY>.pdf` אחרי ניקוי תווים שאסורים בשם קובץ (ROADMAP)."""
    day = day or datetime.now(_ISRAEL).date()
    name = " ".join(_FILENAME_FORBIDDEN.sub(" ", title).split()).strip(" .")
    return f"{name or 'קובץ'} {day.strftime('%d-%m-%Y')}.pdf"


def build_pdf(
    out_path: Path,
    images: Sequence[ProcessedImage],
    layout: DocumentLayout,
    title: str,
    link_url: str | None,
) -> BuildResult:
    """כותב את ה-PDF ל-`out_path`. `images[i]` מתאים ל-`Placement.index == i`."""
    title = clean_title(title)
    total = sum(len(p.placements) for p in layout.pages)
    if total != len(images):
        raise ValueError("layout and images do not match")
    glyphs = _ensure_font()
    removed = "".join(sorted({ch for ch in title if not ch.isspace() and ord(ch) not in glyphs}))
    drawable_title = " ".join("".join(ch for ch in title if ch.isspace() or ord(ch) in glyphs).split())

    page_w = layout.page_width * mm
    page_h = layout.page_height * mm
    c = canvas.Canvas(str(out_path), pagesize=(page_w, page_h), lang="he")
    c.setTitle(title)
    c.setCreator(CREATOR)
    title_size = 0.0
    for page in layout.pages:
        if page.has_header:
            title_size = _draw_header(c, page, page_h, drawable_title, link_url)
        for p in page.placements:
            c.drawImage(
                str(images[p.index].path),
                p.x * mm,
                page_h - (p.y + p.height) * mm,
                width=p.width * mm,
                height=p.height * mm,
            )
        c.showPage()
    c.save()
    if removed:
        logger.info("Title had %d character(s) the font cannot draw; they were left out", len(removed))
    return BuildResult(pages=len(layout.pages), title_font_size=title_size, removed_characters=removed)


def _draw_header(c: canvas.Canvas, page: Page, page_h: float, title: str, link_url: str | None) -> float:
    block_left = page.block_x * mm
    block_right = (page.block_x + page.block_width) * mm
    band_top = page_h - MARGIN_MM * mm
    band_center = band_top - (HEADER_BAND_MM * mm) / 2

    # הלוגו בצד שמאל, ממורכז אנכית ברצועת הכותרת.
    logo_w = 0.0
    if LOGO_PATH.is_file():
        reader = ImageReader(str(LOGO_PATH))
        iw, ih = reader.getSize()
        logo_h = LOGO_HEIGHT_MM * mm
        logo_w = min(logo_h * iw / ih, LOGO_MAX_WIDTH_MM * mm)
        logo_h = logo_w * ih / iw
        c.drawImage(reader, block_left, band_center - logo_h / 2, width=logo_w, height=logo_h, mask="auto")
    else:
        logger.warning("Logo file is missing; the header is drawn without it")

    if not title:
        return 0.0
    visual = get_display(title, base_dir="R")
    width_at_1pt = pdfmetrics.stringWidth(visual, FONT_NAME, 1)
    available = block_right - block_left - logo_w - (TITLE_LOGO_GAP_MM * mm if logo_w else 0)
    size = min(TITLE_MAX_PT, available / width_at_1pt) if width_at_1pt > 0 else TITLE_MAX_PT
    face = pdfmetrics.getFont(FONT_NAME).face
    cap_height = getattr(face, "capHeight", 700) / 1000 * size
    baseline = band_center - cap_height / 2
    text_w = width_at_1pt * size

    c.setFont(FONT_NAME, size)
    c.setFillColorRGB(*TITLE_COLOR)
    c.drawRightString(block_right, baseline, visual)

    # קו תחתון מתחת לירידות של ק/ף/ץ, כדי שלא יחתוך אותן.
    underline_y = baseline - 0.3 * size
    c.setStrokeColorRGB(*UNDERLINE_COLOR)
    c.setLineWidth(0.6)
    c.line(block_right - text_w, underline_y, block_right, underline_y)

    if link_url:
        c.linkURL(
            link_url,
            (block_right - text_w, underline_y - 2, block_right, baseline + cap_height + 2),
            relative=0,
            thickness=0,
        )
    return size
