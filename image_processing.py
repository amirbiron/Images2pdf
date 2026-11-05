"""
image_processing.py — הכנת תמונה אחת ל-PDF: מקובץ מקור בדיסק לקובץ JPEG קטן בדיסק.

הסדר (ROADMAP, "עיבוד התמונות"), והוא חשוב לזיכרון:
1. draft לפני כל פעולה אחרת: מפענח ה-JPEG מקטין כבר בפענוח (פי 2, 4 או 8),
   כך ש-45 מגה-פיקסל לא נכנסים לזיכרון בגודל מלא.
2. thumbnail ל-2000 פיקסלים בצלע הארוכה.
3. רק אחר כך תיקון כיוון לפי EXIF — על התמונה הקטנה.
4. RGB, ושמירה כ-JPEG באיכות 85 עם optimize ו-progressive.
המידות מוחזרות אחרי התיקון, כי מהן מחשבים את הפריסה.

צבע: תמונה עם פרופיל צבע מוטמע (למשל Display P3 של אייפון, או Adobe RGB של מצלמה)
מומרת ל-sRGB לפי הפרופיל. ה-PDF מתייחס לכל JPEG כ-sRGB, ובלי ההמרה הצבעים יוצאים
דהויים. ההמרה נעשית על התמונה הקטנה, אחרי ה-thumbnail.

draft עובד רק ב-JPEG. PNG ענק נטען בגודל מלא, ולכן יש תקרת פיקסלים אחרי draft
(MAX_DECODE_PIXELS): JPEG של 150 מגה-פיקסל עובר (draft מקטין אותו מראש), PNG של
50 מגה-פיקסל נדחה עם הודעה, במקום להפיל את השירות.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

MAX_SIDE = 2000
JPEG_QUALITY = 85
# 40 מגה-פיקסל ב-RGBA הם כ-160MB. מעבר לזה, יחד עם בסיס התהליך, מתקרבים ל-512MB.
MAX_DECODE_PIXELS = 40_000_000


class ImageProcessingError(Exception):
    """התמונה לא עובדה. `reason` הוא הסבר בעברית למשתמש; הפרטים הטכניים בלוג."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ProcessedImage:
    name: str
    path: Path
    width: int
    height: int

    @property
    def aspect(self) -> float:
        """רוחב/גובה אחרי תיקון הכיוון — הערך שהפריסה מקבלת."""
        return self.width / self.height


def process_image(src: Path, dst: Path, name: str) -> ProcessedImage:
    """מעבד את `src` ל-`dst` (JPEG). זורק ImageProcessingError בכל כשל."""
    if dst.suffix.lower() not in (".jpg", ".jpeg"):
        # ReportLab מטמיע קובץ JPEG כמו שהוא רק לפי הסיומת; אחרת הוא מפענח אותו שוב.
        # מקור: reportlab/pdfbase/pdfdoc.py, PDFImageXObject.__init__ (ext in ('.jpg', '.jpeg'))
        raise ValueError("destination must have a .jpg extension")
    try:
        with Image.open(src) as img:
            img.draft("RGB", (MAX_SIDE, MAX_SIDE))
            if img.width * img.height > MAX_DECODE_PIXELS:
                raise ImageProcessingError(
                    f"התמונה גדולה מדי לעיבוד ({img.width}×{img.height} פיקסלים)."
                )
            img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
            ImageOps.exif_transpose(img, in_place=True)
            rgb = _to_srgb(img)
            try:
                rgb.save(dst, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
                width, height = rgb.size
            finally:
                if rgb is not img:
                    rgb.close()
    except ImageProcessingError:
        raise
    except Image.DecompressionBombError:
        # Pillow עוצר כבר בפתיחה מעל ~179 מגה-פיקסל (2 × MAX_IMAGE_PIXELS).
        logger.warning("Image rejected by Pillow's decompression bomb limit")
        raise ImageProcessingError("התמונה גדולה מדי (מעל 179 מגה-פיקסל).") from None
    except UnidentifiedImageError:
        raise ImageProcessingError("הקובץ אינו תמונה שאפשר לקרוא.") from None
    except (OSError, ValueError, SyntaxError) as exc:
        # קובץ חתוך או פגום. Pillow זורק OSError על פענוח שנכשל ("image file is truncated"),
        # ו-ValueError/SyntaxError על מבנה פגום בחלק מהפורמטים. ההודעה של Pillow לא
        # כוללת נתוני משתמש, ולכן היא נרשמת בלוג; למשתמש — הודעה כללית עם שם הקובץ.
        logger.warning("Image decode failed: %s: %s", type(exc).__name__, exc)
        raise ImageProcessingError("קובץ התמונה פגום או חתוך.") from None
    if width < 1 or height < 1:
        raise ImageProcessingError("לתמונה אין מידות תקינות.")
    return ProcessedImage(name=name, path=dst, width=width, height=height)


def _to_srgb(img: Image.Image) -> Image.Image:
    """RGB במרחב sRGB. כשיש פרופיל צבע מוטמע — המרה לפיו; אחרת המרה רגילה.

    כשל בהמרה לפי הפרופיל (פרופיל פגום, או פרופיל שלא מתאים למצב התמונה) לא מפיל את
    התמונה: נרשם בלוג, והתמונה עוברת בהמרה הרגילה — עם הצבעים שהיו בלי התיקון.
    מקור ל-API: PIL/ImageCms.py, profileToProfile ו-createProfile("sRGB").
    """
    icc = img.info.get("icc_profile")
    if isinstance(icc, bytes) and icc:
        try:
            source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            srgb = ImageCms.createProfile("sRGB")
            if img.mode == "CMYK":
                return ImageCms.profileToProfile(img, source, srgb, outputMode="RGB")
            rgb = _to_rgb(img)
            try:
                return ImageCms.profileToProfile(rgb, source, srgb, outputMode="RGB")
            finally:
                if rgb is not img:
                    rgb.close()
        except (ImageCms.PyCMSError, OSError, ValueError) as exc:
            logger.warning("ICC conversion to sRGB failed (%s); using a plain RGB conversion", type(exc).__name__)
    return _to_rgb(img)


def _to_rgb(img: Image.Image) -> Image.Image:
    """המרה ל-RGB. שקיפות מונחת על רקע לבן, אחרת היא יוצאת שחורה.

    גם תמונת RGB יכולה לשאת שקיפות: PNG עם צבע שקוף (tRNS) נשאר במצב RGB, והצבע
    השקוף שמור ב-info["transparency"]. לכן המסלול המהיר רק כשאין שקיפות כלל.
    """
    if img.mode == "RGB" and "transparency" not in img.info:
        return img
    has_alpha = "A" in img.getbands() or "transparency" in img.info
    if has_alpha:
        rgba = img.convert("RGBA")
        try:
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.getchannel("A"))
            return background
        finally:
            rgba.close()
    return img.convert("RGB")
