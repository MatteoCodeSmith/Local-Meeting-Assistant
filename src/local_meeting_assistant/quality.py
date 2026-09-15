"""Conservative checks for the Italian/English transcript, never edits the raw file."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

ROW = re.compile(r"^\*\*(\d+:\d+(?::\d+)?) — (.*?):\*\* (.+)$")


@dataclass(frozen=True)
class SourceLine:
    id: int
    time: str
    speaker: str
    text: str


def normalized(text: str) -> str:
    return " ".join(text.casefold().split())


def unexpected_script(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    foreign = sum("LATIN" not in unicodedata.name(char, "") for char in letters)
    return len(letters) >= 2 and foreign / len(letters) > 0.25


def recap_sources(transcript: str) -> tuple[list[SourceLine], list[str], bool]:
    rows = [match.groups() for line in transcript.splitlines() if (match := ROW.match(line))]
    if not rows and not transcript.lstrip().startswith("#"):
        rows = [("--:--", "Non identificato", transcript.strip())] if transcript.strip() else []
    counts = Counter(normalized(text) for _, _, text in rows)
    accepted: list[SourceLine] = []
    suspicious = 0
    for index, (timestamp, speaker, text) in enumerate(rows, 1):
        repeated = len(text.split()) >= 5 and counts[normalized(text)] >= 4
        if unexpected_script(text) or repeated or text.startswith("[Parlato non affidabile:"):
            suspicious += 1
            continue
        if not re.search(r"\w", text) or normalized(text).strip(".!?, ") in {
            "um", "uh", "eh", "grazie", "thank you", "you",
        }:
            continue
        # Keep short negations/confirmations as context: dropping "No" changes decisions.
        accepted.append(SourceLine(index, timestamp, speaker, text))
    warnings = []
    if suspicious:
        warnings.append(
            f"Esclusi dal recap {suspicious}/{len(rows)} passaggi sospetti "
            "(ripetizioni o scrittura estranea a italiano/inglese). "
            "La trascrizione originale e l'audio restano la fonte da verificare."
        )
    blocked = not accepted
    if blocked:
        warnings.append("Trascrizione insufficiente o troppo sospetta: recap non affidabile.")
    return accepted, warnings, blocked
