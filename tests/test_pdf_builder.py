"""כתיבת ה-PDF: קישור בכותרת (pypdf), מטא-דאטה, כותרות בעברית, כותרת ארוכה ושם קובץ."""

from __future__ import annotations

import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest
from bidi import get_display
from PIL import Image
from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics

import pdf_builder
from image_processing import process_image
from pdf_builder import FONT_NAME, TITLE_MAX_PT, build_pdf, clean_title, folder_url, pdf_filename
from pdf_layout import layout_document

FOLDER = "1AbC_dEf-123"


def _images(tmp_path: Path, ratios):
    out = []
    for i, (w, h) in enumerate(ratios):
        src = tmp_path / f"src{i}.jpg"
        Image.new("RGB", (w * 200, h * 200), (40 * i % 255, 120, 200)).save(src, quality=90)
        out.append(process_image(src, tmp_path / f"img{i}.jpg", src.name))
    return out


def _build(tmp_path, title, ratios=((3, 2), (2, 3), (1, 1))):
    images = _images(tmp_path, ratios)
    layout = layout_document([i.aspect for i in images])
    out = tmp_path / "out.pdf"
    result = build_pdf(out, images, layout, title, folder_url(FOLDER))
    return out, layout, result


def test_title_link_and_metadata(tmp_path):
    title = "וילה 7 בכרמל – Villa Carmel (2026)"
    out, layout, result = _build(tmp_path, title)
    reader = PdfReader(out)
    assert reader.metadata.title == title
    links = [a.get_object() for a in reader.pages[0].get("/Annots", [])]
    uris = [a["/A"]["/URI"] for a in links if a.get("/Subtype") == "/Link"]
    assert uris == [f"https://drive.google.com/drive/folders/{FOLDER}"]
    # הקישור יושב ברצועת הכותרת, בראש העמוד
    rect = [float(v) for v in links[0]["/Rect"]]
    page_h = float(reader.pages[0].mediabox.height)
    assert rect[1] > page_h - 34 * 72 / 25.4
    # בעמודים הבאים אין כותרת ואין קישור
    for page in reader.pages[1:]:
        assert not page.get("/Annots")
    assert result.pages == len(layout.pages) == len(reader.pages)


def test_long_title_shrinks_to_fit_one_line(tmp_path):
    title = "וילה עם בריכה ונוף לים בקיסריה – Caesarea Sea View Villa – כולל חדרי אמבטיה וגינה"
    _, layout, result = _build(tmp_path, title)
    assert result.title_font_size < TITLE_MAX_PT
    page = layout.pages[0]
    width = pdfmetrics.stringWidth(get_display(title, base_dir="R"), FONT_NAME, result.title_font_size)
    assert width <= page.block_width * 72 / 25.4  # שורה אחת בתוך רוחב הגוש


def test_short_title_keeps_max_size(tmp_path):
    _, _, result = _build(tmp_path, "גן עדן")
    assert result.title_font_size == TITLE_MAX_PT


def test_characters_without_glyphs_are_removed_and_reported(tmp_path):
    out, _, result = _build(tmp_path, "וילה 🌊 בים")
    assert result.removed_characters == "🌊"
    assert "🌊" not in PdfReader(out).pages[0].extract_text()


@pytest.mark.parametrize("bad", ["", "   ", None, 5, "א" * 121])
def test_invalid_titles_are_refused(bad):
    with pytest.raises(ValueError):
        clean_title(bad)


def test_title_is_cleaned_not_truncated():
    assert clean_title("  וילה\tבכרמל\n ") == "וילה בכרמל"
    assert clean_title("א" * 120) == "א" * 120


def test_folder_url_validates_id():
    assert folder_url("abc_D-1") == "https://drive.google.com/drive/folders/abc_D-1"
    with pytest.raises(ValueError):
        folder_url("abc' or 1=1")


@pytest.mark.parametrize(
    "title, expected",
    [
        ("וילה בכרמל", "וילה בכרמל 05-11-2026.pdf"),
        ('צימר "הגליל": חדר/2 <A>?', "צימר הגליל חדר 2 A 05-11-2026.pdf"),
        ("..", "קובץ 05-11-2026.pdf"),
        ("ab" + chr(0x202E) + "fdp.exe", "ab fdp.exe 05-11-2026.pdf"),
    ],
)
def test_pdf_filename(title, expected):
    assert pdf_filename(title, date(2026, 11, 5)) == expected


def test_mismatched_layout_is_refused(tmp_path):
    images = _images(tmp_path, ((1, 1), (1, 1)))
    with pytest.raises(ValueError):
        build_pdf(tmp_path / "x.pdf", images, layout_document([1.0]), "כותרת", None)


def test_identical_images_are_stored_once(tmp_path):
    """ReportLab שומר תמונה זהה פעם אחת — לכן בדיקת משקל צריכה תמונות שונות (ROADMAP).

    משווים קובץ עם התמונה פעם אחת לקובץ עם אותה תמונה ארבע פעמים: ההפרש קטן
    ממשקל התמונה עצמה (הפונט והלוגו הם תקורה קבועה בשני הקבצים).
    """
    src = tmp_path / "noise.jpg"
    Image.effect_noise((1200, 800), 80).convert("RGB").save(src, quality=90)
    image = process_image(src, tmp_path / "img.jpg", src.name)
    sizes = []
    for count in (1, 4):
        out = tmp_path / f"x{count}.pdf"
        build_pdf(out, [image] * count, layout_document([image.aspect] * count), "כותרת", None)
        sizes.append(out.stat().st_size)
    assert sizes[1] - sizes[0] < image.path.stat().st_size / 2


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="pdftoppm (poppler) לא מותקן")
def test_page_renders_with_header_pixels(tmp_path):
    """צילום של העמוד: יש דיו ברצועת הכותרת, בצד ימין (כותרת) ובצד שמאל (לוגו)."""
    out, layout, _ = _build(tmp_path, "וילה 7 בכרמל – Villa Carmel (2026)")
    subprocess.run(["pdftoppm", "-r", "72", "-f", "1", "-l", "1", "-png", str(out), str(tmp_path / "p")], check=True)
    png = next(tmp_path.glob("p*.png"))
    with Image.open(png) as img:
        gray = img.convert("L")
        w, h = gray.size
        px_per_mm = w / layout.page_width
        band = gray.crop((0, int(12 * px_per_mm), w, int(28 * px_per_mm)))
        right = band.crop((w // 2, 0, w, band.height))
        left = band.crop((0, 0, w // 2, band.height))
        assert right.getextrema()[0] < 80, "אין טקסט כותרת בצד ימין"
        assert left.getextrema()[0] < 200, "אין לוגו בצד שמאל"


def test_font_registration_is_retried_after_failure(monkeypatch, tmp_path):
    """כשל ברישום הפונט לא משאיר את השומר מוצב (אחרת כל קריאה הבאה הייתה מדלגת)."""
    monkeypatch.setattr(pdf_builder, "_font_glyphs", None)
    monkeypatch.setattr(pdf_builder, "FONT_PATH", tmp_path / "missing.ttf")
    with pytest.raises(Exception):
        pdf_builder._ensure_font()
    assert pdf_builder._font_glyphs is None
    monkeypatch.undo()
    assert pdf_builder._ensure_font()
