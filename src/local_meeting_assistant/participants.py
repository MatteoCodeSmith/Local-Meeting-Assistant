"""Explicit, session-scoped speaker labels; no biometric identity inference."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from .domain import SessionRecord, TranscriptSegment, transcript_as_markdown


def clean_name(value: str) -> str:
    value = " ".join(value.split())
    if len(value) > 100 or any(char in value for char in "[]<>*\n\r"):
        raise ValueError("Usa un nome di massimo 100 caratteri, senza marcatori Markdown.")
    return value


def segment_key(segment: TranscriptSegment) -> str:
    # Text hash prevents an old manual assignment being silently reused after re-decoding.
    digest = hashlib.sha256(segment.text.encode("utf-8")).hexdigest()[:16]
    return f"{segment.track}:{segment.start:.3f}:{segment.end:.3f}:{digest}"


@dataclass
class Participants:
    local_name: str = ""
    microphone_is_me: bool = False
    remote_name: str = ""
    single_remote: bool = False
    overrides: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, directory: Path) -> Participants:
        path = directory / "participants.json"
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            clean_name(data.get("local_name", "")),
            bool(data.get("microphone_is_me")),
            clean_name(data.get("remote_name", "")),
            bool(data.get("single_remote")),
            {str(key): clean_name(name) for key, name in data.get("overrides", {}).items()},
        )

    def save(self, directory: Path):
        self.local_name, self.remote_name = (
            clean_name(self.local_name),
            clean_name(self.remote_name),
        )
        if self.single_remote and not self.remote_name:
            raise ValueError("Inserisci il nome dell'unico interlocutore remoto.")
        payload = {
            "version": 1,
            "local_name": self.local_name,
            "microphone_is_me": self.microphone_is_me,
            "remote_name": self.remote_name,
            "single_remote": self.single_remote,
            "overrides": self.overrides,
        }
        target = directory / "participants.json"
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(target)

    def apply(self, segment: TranscriptSegment) -> TranscriptSegment:
        name = self.overrides.get(segment_key(segment), "")
        if not name and segment.track == "mic" and self.microphone_is_me:
            name = self.local_name
        if not name and segment.track == "system" and self.single_remote:
            name = self.remote_name
        return replace(segment, speaker=name, speaker_source="manual") if name else segment


def read_segments(directory: Path) -> list[TranscriptSegment]:
    path = directory / "transcript.json"
    if not path.exists():
        return []
    return [TranscriptSegment(**row) for row in json.loads(path.read_text(encoding="utf-8"))]


def backup_transcript(record: SessionRecord) -> None:
    names = [
        name
        for name in ("transcript.json", "transcript.md", "recap.md", "participants.json")
        if (record.directory / name).exists()
    ]
    if names:
        target = record.directory / "revisions" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        target.mkdir(parents=True)
        for name in names:
            shutil.copy2(record.directory / name, target / name)


def relabel_transcript(record: SessionRecord, participants: Participants, repository) -> None:
    segments = read_segments(record.directory)
    if not segments:
        return
    # Retain the original attribution so clearing a manual name is reversible.
    baseline = record.directory / "speaker-baseline.json"
    if baseline.exists():
        originals = json.loads(baseline.read_text(encoding="utf-8"))
    else:
        originals = {}
    for segment in segments:
        key = segment_key(segment)
        originals.setdefault(
            key, {"speaker": segment.speaker, "speaker_source": segment.speaker_source}
        )
    temp = baseline.with_suffix(".tmp")
    temp.write_text(json.dumps(originals, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(baseline)
    restored = [replace(segment, **originals[segment_key(segment)]) for segment in segments]
    from .voices import apply_saved_voice_names
    restored = apply_saved_voice_names(record.directory, restored)
    labelled = [participants.apply(segment) for segment in restored]
    repository.save_transcript(record, labelled, transcript_as_markdown(record, labelled))
