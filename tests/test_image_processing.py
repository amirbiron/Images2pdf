"""עיבוד תמונה: סיבוב לפי EXIF, CMYK, קובץ פגום, תמונה ענקית ושקיפות."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from image_processing import MAX_SIDE, ImageProcessingError, process_image

ROOT = Path(__file__).resolve().parent.parent


def _jpeg(path: Path, size, color=(200, 30, 30), exif_orientation: int | None = None, mode="RGB"):
    img = Image.new(mode, size, color)
    kwargs = {"quality": 90}
    if exif_orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        kwargs["exif"] = exif.tobytes()
    img.save(path, "JPEG", **kwargs)
    return path


def test_output_is_small_progressive_rgb_jpeg(tmp_path):
    src = _jpeg(tmp_path / "big.jpg", (6000, 4000))
    out = process_image(src, tmp_path / "out.jpg", "big.jpg")
    assert (out.width, out.height) == (2000, 1333)
    with Image.open(out.path) as img:
        assert img.format == "JPEG" and img.mode == "RGB"
        assert max(img.size) == MAX_SIDE
        assert img.info.get("progressive") or img.info.get("progression")


def test_exif_rotation_is_applied_after_resize(tmp_path):
    # התמונה שמורה לרוחב: חצי שמאל אדום, חצי ימין כחול. Orientation=6 אומר "סובב 90°
    # עם כיוון השעון כדי להציג" — אחרי התיקון היא לאורך, האדום למעלה והכחול למטה.
    img = Image.new("RGB", (4000, 2000), (220, 20, 20))
    img.paste((20, 20, 220), (2000, 0, 4000, 2000))
    exif = Image.Exif()
    exif[0x0112] = 6
    src = tmp_path / "rotated.jpg"
    img.save(src, "JPEG", quality=95, exif=exif.tobytes())

    out = process_image(src, tmp_path / "out.jpg", "rotated.jpg")
    assert (out.width, out.height) == (1000, 2000)  # המידות אחרי התיקון — מהן מחשבים את הפריסה
    with Image.open(out.path) as result:
        top = result.getpixel((500, 200))
        bottom = result.getpixel((500, 1800))
        assert top[0] > 150 and top[2] < 80, top
        assert bottom[2] > 150 and bottom[0] < 80, bottom
        assert result.getexif().get(0x0112) in (None, 1)  # לא מסובבים פעמיים בצפייה


def test_cmyk_is_converted_to_rgb_with_sane_colours(tmp_path):
    src = tmp_path / "cmyk.jpg"
    Image.new("CMYK", (800, 600), (0, 255, 255, 0)).save(src, "JPEG", quality=95)  # אדום בדפוס
    out = process_image(src, tmp_path / "out.jpg", "cmyk.jpg")
    with Image.open(out.path) as result:
        assert result.mode == "RGB"
        r, g, b = result.getpixel((400, 300))
        assert r > 200 and g < 60 and b < 60, (r, g, b)


def test_not_an_image_is_reported(tmp_path):
    src = tmp_path / "notes.jpg"
    src.write_bytes(os.urandom(5000))
    with pytest.raises(ImageProcessingError) as info:
        process_image(src, tmp_path / "out.jpg", "notes.jpg")
    assert "אינו תמונה" in info.value.reason


def test_truncated_jpeg_is_reported(tmp_path):
    good = _jpeg(tmp_path / "good.jpg", (3000, 2000))
    data = good.read_bytes()
    src = tmp_path / "cut.jpg"
    src.write_bytes(data[: len(data) // 3])
    with pytest.raises(ImageProcessingError) as info:
        process_image(src, tmp_path / "out.jpg", "cut.jpg")
    assert "פגום" in info.value.reason
    assert not (tmp_path / "out.jpg").exists() or (tmp_path / "out.jpg").stat().st_size == 0


def test_huge_png_is_rejected_before_decoding(tmp_path):
    src = tmp_path / "huge.png"
    Image.new("RGB", (8000, 6000), (10, 120, 10)).save(src, "PNG")  # 48MP, draft לא עובד ב-PNG
    with pytest.raises(ImageProcessingError) as info:
        process_image(src, tmp_path / "out.jpg", "huge.png")
    assert "גדולה מדי" in info.value.reason


def test_transparent_png_gets_white_background(tmp_path):
    src = tmp_path / "logo.png"
    Image.new("RGBA", (400, 300), (0, 0, 0, 0)).save(src, "PNG")
    out = process_image(src, tmp_path / "out.jpg", "logo.png")
    with Image.open(out.path) as result:
        assert min(result.getpixel((200, 150))) > 245


def test_destination_must_be_jpg(tmp_path):
    src = _jpeg(tmp_path / "a.jpg", (100, 100))
    with pytest.raises(ValueError):
        process_image(src, tmp_path / "out.png", "a.jpg")


# שיא הזיכרון נמדד ב-VmHWM של התהליך החדש, ולא ב-ru_maxrss: ב-Linux ru_maxrss של תהליך
# בן יורש את ה-RSS של האב ברגע ה-fork (נמדד: אב של 512MB → בן "של 512MB" שבפועל 10MB).
_MEASURE = r"""
import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from image_processing import process_image
out = process_image(Path(sys.argv[2]), Path(sys.argv[3]), "x")
hwm_kb = int(next(l for l in open("/proc/self/status") if l.startswith("VmHWM")).split()[1])
print(out.width, out.height, hwm_kb // 1024)
"""


@pytest.mark.slow
def test_image_over_100_megapixels_is_processed_with_low_memory(tmp_path):
    """JPEG של 108 מגה-פיקסל: draft מפענח אותו ברבע מהגודל, ולכן הזיכרון נשאר נמוך.

    המדידה בתהליך נפרד, כדי שהזיכרון של יצירת התמונה בבדיקה לא ייספר.
    """
    src = tmp_path / "108mp.jpg"
    big = Image.effect_noise((12000, 9000), 60).convert("RGB")
    big.save(src, "JPEG", quality=85)
    del big
    proc = subprocess.run(
        [sys.executable, "-c", _MEASURE, str(ROOT), str(src), str(tmp_path / "out.jpg")],
        capture_output=True, text=True, check=True,
    )
    width, height, peak_mb = map(int, proc.stdout.split())
    assert (width, height) == (2000, 1500)
    print(f"108MP JPEG: peak RSS {peak_mb}MB")
    assert peak_mb < 200, f"peak RSS {peak_mb}MB"


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_display_p3_photo_is_converted_to_srgb(tmp_path):
    """תמונה מאייפון מסומנת Display P3. ה-PDF מתייחס לכל JPEG כ-sRGB, ולכן בלי המרה
    הצבע (200,100,50) היה נשאר כמו שהוא ונראה דהוי. אחרי ההמרה הוא רווי יותר."""
    icc = (FIXTURES / "DisplayP3-v4.icc").read_bytes()
    src = tmp_path / "iphone.jpg"
    Image.new("RGB", (1200, 900), (200, 100, 50)).save(src, "JPEG", quality=95, icc_profile=icc)
    out = process_image(src, tmp_path / "out.jpg", "iphone.jpg")
    with Image.open(out.path) as result:
        r, g, b = result.getpixel((600, 450))
        assert r >= 210 and g <= 96 and b <= 36, (r, g, b)  # ImageCms: (215, 93, 31)
        assert not result.info.get("icc_profile")  # הפלט הוא sRGB לא מסומן, כמו שה-PDF מניח


def test_broken_icc_profile_falls_back_to_plain_conversion(tmp_path):
    src = tmp_path / "bad-profile.jpg"
    Image.new("RGB", (800, 600), (200, 100, 50)).save(src, "JPEG", quality=95, icc_profile=b"not an icc profile at all")
    out = process_image(src, tmp_path / "out.jpg", "bad-profile.jpg")
    with Image.open(out.path) as result:
        r, g, b = result.getpixel((400, 300))
        assert abs(r - 200) < 6 and abs(g - 100) < 6 and abs(b - 50) < 6, (r, g, b)


def test_rgb_png_with_transparent_colour_gets_white_background(tmp_path):
    """PNG במצב RGB עם צבע שקוף (tRNS): Pillow משאיר אותו RGB ושומר את הצבע ב-info."""
    src = tmp_path / "trns.png"
    img = Image.new("RGB", (400, 300), (10, 20, 30))
    img.save(src, "PNG", transparency=(10, 20, 30))
    with Image.open(src) as check:
        assert check.mode == "RGB" and check.info.get("transparency") == (10, 20, 30)
    out = process_image(src, tmp_path / "out.jpg", "trns.png")
    with Image.open(out.path) as result:
        assert min(result.getpixel((200, 150))) > 245
