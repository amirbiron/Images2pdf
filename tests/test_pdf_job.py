"""יצירה מקצה לקצה: תמונה שנכשלה מדווחת בשמה, והקובץ נוצר מהשאר."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader

from pdf_job import FetchError, SourceItem, run_pdf_job


def _fixture_dir(tmp_path: Path) -> Path:
    src = tmp_path / "drive"
    src.mkdir()
    Image.new("RGB", (1500, 1000), (200, 40, 40)).save(src / "a.jpg", quality=90)
    Image.new("RGB", (1000, 1500), (40, 200, 40)).save(src / "b.jpg", quality=90)
    (src / "broken.jpg").write_bytes(os.urandom(3000))
    return src


def _fetcher(src: Path):
    def fetch(item: SourceItem, dest: Path) -> int:
        if item.name == "missing.jpg":
            raise FetchError("ההורדה מדרייב נכשלה (HTTP 404).")
        shutil.copyfile(src / item.name, dest)
        return dest.stat().st_size

    return fetch


def test_failed_files_are_reported_by_name_and_the_rest_is_built(tmp_path):
    src = _fixture_dir(tmp_path)
    items = [SourceItem("1", "a.jpg"), SourceItem("2", "broken.jpg"), SourceItem("3", "missing.jpg"), SourceItem("4", "b.jpg")]
    out = tmp_path / "out.pdf"
    result = run_pdf_job(items, _fetcher(src), title="וילה", link_url=None, out_path=out, work_dir=tmp_path / "work")
    assert result.ok and result.processed == 2
    assert [name for name, _ in result.failed] == ["broken.jpg", "missing.jpg"]
    assert "אינו תמונה" in result.failed[0][1] and "404" in result.failed[1][1]
    assert len(PdfReader(out).pages) == 1
    assert result.pdf_bytes == out.stat().st_size
    assert list((tmp_path / "work").glob("source-*")) == []  # קבצי המקור נמחקו אחרי כל תמונה
    assert result.memory is not None and result.memory.samples >= 1
    assert result.seconds_total >= result.seconds_process


def test_nothing_processed_means_no_file(tmp_path):
    src = _fixture_dir(tmp_path)
    out = tmp_path / "out.pdf"
    result = run_pdf_job([SourceItem("2", "broken.jpg")], _fetcher(src), title="וילה", link_url=None, out_path=out, work_dir=tmp_path / "w")
    assert not result.ok and result.failed and not out.exists()


@pytest.mark.parametrize("count", [0, 13])
def test_image_count_limits(tmp_path, count):
    with pytest.raises(ValueError):
        run_pdf_job([SourceItem(str(i), "a.jpg") for i in range(count)], _fetcher(tmp_path), title="x", link_url=None,
                    out_path=tmp_path / "o.pdf", work_dir=tmp_path / "w")


def test_failed_build_leaves_previous_file_untouched(tmp_path):
    src = _fixture_dir(tmp_path)
    out = tmp_path / "out.pdf"
    out.write_bytes(b"previous")
    with pytest.raises(ValueError):
        run_pdf_job([SourceItem("1", "a.jpg")], _fetcher(src), title="א" * 500, link_url=None, out_path=out, work_dir=tmp_path / "w")
    assert out.read_bytes() == b"previous"
