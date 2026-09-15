"""Session-local voice grouping and conservative alignment; no cloud or global identity DB."""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from .domain import TranscriptSegment, transcript_as_markdown
from .participants import Participants, backup_transcript, clean_name, read_segments, segment_key


def load_analysis(directory: Path) -> dict:
    path = directory / "voices.json"
    if not path.exists():
        path = directory / "live-voices.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def match_turn(segment: TranscriptSegment, turns: list[dict]) -> str | None:
    """Never invent word timing or label a segment containing several audible speakers."""
    durations = defaultdict(float)
    for turn in turns:
        if turn["track"] == segment.track:
            overlap = max(0.0, min(segment.end, turn["end"]) - max(segment.start, turn["start"]))
            durations[turn["speaker"]] += overlap
    durations = {key: value for key, value in durations.items() if value > 0}
    if not durations or segment.end <= segment.start:
        return None
    speaker = max(durations, key=durations.get)
    total = sum(durations.values())
    if (any(value >= 0.15 for key, value in durations.items() if key != speaker)
            or durations[speaker] / total < 0.85
            or durations[speaker] / (segment.end - segment.start) < 0.5):
        return None
    return speaker


def attribute_live_segment(segment: TranscriptSegment, data: dict, participants: Participants):
    """Recompute from the original passage, never from a previously guessed name."""
    manual = participants.apply(segment)
    if manual.speaker_source == "manual":
        return manual
    speaker = match_turn(segment, data.get("turns", []))
    name = data.get("names", {}).get(speaker, "")
    return replace(segment, speaker=name, speaker_source="voice_profile") if name else segment


def apply_voice_names(record, names: dict[str, str], repository) -> tuple[int, int]:
    """Only an explicit user confirmation updates attribution, not text/timing or recaps."""
    data = load_analysis(record.directory)
    if not data:
        raise ValueError("Analizza prima le voci della registrazione.")
    allowed = {turn["speaker"] for turn in data["turns"]}
    names = {key: clean_name(value) for key, value in names.items() if key in allowed}
    data["names"] = names
    segments = read_segments(record.directory)
    participants = Participants.load(record.directory)
    baseline = record.directory / "speaker-baseline.json"
    originals = json.loads(baseline.read_text(encoding="utf-8")) if baseline.exists() else {}
    updated, assigned, uncertain = [], 0, 0
    for segment in segments:
        key = segment_key(segment)
        originals.setdefault(key, {"speaker": segment.speaker, "speaker_source": segment.speaker_source})
        # Explicit passage corrections and single-person declarations always win.
        manual = participants.apply(replace(segment, **originals[key]))
        if manual.speaker_source == "manual":
            updated.append(manual)
            continue
        speaker = match_turn(segment, data["turns"])
        name = names.get(speaker, "")
        if name:
            updated.append(replace(segment, speaker=name, speaker_source="voice_confirmed"))
            assigned += 1
        else:
            updated.append(replace(segment, **originals[key]))
            uncertain += 1
    backup_transcript(record)
    if (record.directory / "voices.json").exists():
        from datetime import datetime
        backup = record.directory / "voice-runs" / f"review-{datetime.now():%Y%m%d-%H%M%S-%f}.json"
        backup.parent.mkdir(parents=True, exist_ok=True)
        repository._write_json(backup, load_analysis(record.directory))
    repository._write_json(baseline, originals)
    repository._write_json(record.directory / "voices.json", data)
    if segments:
        repository.save_transcript(record, updated, transcript_as_markdown(record, updated))
    return assigned, uncertain


def apply_saved_voice_names(directory: Path, segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    data = load_analysis(directory)
    if not data:
        return segments
    result = []
    for segment in segments:
        speaker = match_turn(segment, data["turns"])
        name = data.get("names", {}).get(speaker, "")
        source = "voice_profile" if data.get("mode") == "live" else "voice_confirmed"
        result.append(replace(segment, speaker=name, speaker_source=source)
                      if name and segment.speaker_source != "manual" else segment)
    return result
