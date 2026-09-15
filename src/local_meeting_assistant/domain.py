from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any


class SessionSource(StrEnum):
    TEAMS = "teams"
    MANUAL = "manual"


class SessionStatus(StrEnum):
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    SUMMARIZING = "summarizing"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(slots=True)
class TranscriptSegment:
    start: float
    end: float
    text: str
    speaker: str
    track: str
    language: str | None = None
    speaker_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SessionRecord:
    session_id: str
    directory: Path
    source: SessionSource
    status: SessionStatus = SessionStatus.RECORDING
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime | None = None
    title: str = "Conversazione"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "directory": str(self.directory),
            "source": self.source.value,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "title": self.title,
            "error": self.error,
        }


def merge_segments(segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    """Return deterministic chronological transcript order."""
    return sorted(segments, key=lambda item: (item.start, item.track, item.end))


def transcript_as_markdown(record: SessionRecord, segments: list[TranscriptSegment]) -> str:
    lines = [
        f"# Trascrizione — {record.title}",
        "",
        f"- Inizio: {record.started_at.astimezone().isoformat(timespec='seconds')}",
        f"- Origine: {record.source.value}",
        "",
    ]
    if any(segment.speaker_source == "teams_uia_approximate" for segment in segments):
        lines += ["> Nomi Teams: associazione temporale indicativa dagli indicatori dell'interfaccia, "
                  "non identificazione vocale. Verificare le attribuzioni importanti.", ""]
    if any(segment.speaker_source == "manual" for segment in segments):
        lines += ["> Le etichette dei parlanti indicate come manuali nel JSON sono state "
                  "inserite dall'utente, non riconosciute dalla voce.", ""]
    if any(segment.speaker_source == "voice_confirmed" for segment in segments):
        lines += ["> Nomi associati dall'utente a gruppi vocali stimati localmente. "
                  "Le attribuzioni automatiche ai passaggi possono richiedere correzioni.", ""]
    if any(segment.speaker_source == "voice_profile" for segment in segments):
        lines += ["> Nomi stimati per somiglianza con la rubrica vocale locale: "
                  "non sono identificazioni certe. Verificare le attribuzioni importanti.", ""]
    for segment in merge_segments(segments):
        minutes, seconds = divmod(max(0, int(segment.start)), 60)
        clean = " ".join(segment.text.split())
        lines.append(f"**{minutes:02d}:{seconds:02d} — {segment.speaker}:** {clean}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
