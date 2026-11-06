"""אין תווי כיווניות (BiDi) נסתרים בקבצי המקור.

התווים כתובים כאן כ-escape, אחרת הבדיקה הייתה נכשלת על עצמה.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIDI = {chr(c) for c in (0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A))}
# קבצי קוד והגדרות בלבד. קבצי ה-Markdown (ROADMAP, CLAUDE.md) נכתבים בדפדפן ואינם קוד שרץ.
SUFFIXES = {".py", ".html", ".js", ".mjs", ".css", ".yaml", ".yml", ".txt", ".toml", ".cfg"}


def test_no_bidi_control_characters():
    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in SUFFIXES or any(p in path.parts for p in (".git", "venv", ".venv")):
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(ch in BIDI for ch in line):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}")
    assert offenders == []
