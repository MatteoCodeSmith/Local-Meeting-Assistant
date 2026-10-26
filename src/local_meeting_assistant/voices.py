"""Session-local voice grouping and conservative alignment; no cloud or global identity DB."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from .domain import TranscriptSegment, transcript_as_markdown
from .participants import Participants, backup_transcript, clean_name, read_segments, segment_key
from .voice_corrections import VoiceErrorDatabase, filtered_analysis, overlaps


def load_analysis(directory: Path) -> dict:
    path = directory / "voices.json"
    if not path.exists():
        path = directory / "live-voices.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not data:
        return data
    try:
        exclusions = VoiceErrorDatabase.for_session(directory).list(directory)
    except (OSError, sqlite3.Error):
        # Fail closed for names, never block Whisper/transcript saving because
        # this optional database is locked or damaged. Do not bypass corrections.
        return {**data, "turns": [], "groups": [], "names": {}, "corrections_unavailable": True,
                "warning": "Blacklist non leggibile: attribuzioni vocali sospese. Testo conservato."}
    return filtered_analysis(data, exclusions)


def without_voice_guess(segment):
    if segment.speaker_source in ("voice_profile", "voice_confirmed"):
        return replace(segment, speaker="Ambiente" if segment.track == "mic" else "Remoti",
                       speaker_source=None)
    return segment


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


def apply_voice_names(record, names: dict[str, str], repository, confirm=True) -> tuple[int, int]:
    """Update attribution only, never text/timing or recaps. confirm=False keeps names that
    came from the voice book marked as automatic instead of user-confirmed."""
    data = load_analysis(record.directory)
    if not data:
        raise ValueError("Analizza prima le voci della registrazione.")
    if data.get("corrections_unavailable"):
        raise ValueError(data["warning"])
    raw_path = record.directory / "voices.json"
    if not raw_path.exists():
        raw_path = record.directory / "live-voices.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    allowed = {turn["speaker"] for turn in raw["turns"]}
    visible = {turn["speaker"] for turn in data["turns"]}
    # Preserve hidden groups' confirmed names so undo restores their attribution.
    hidden_names = {key: value for key, value in raw.get("names", {}).items()
                    if key in allowed and key not in visible}
    names = {key: clean_name(value) for key, value in {**hidden_names, **names}.items() if key in allowed}
    automatic = {} if confirm else {key: value for key, value in raw.get("auto_names", {}).items()
                                    if names.get(key) == value.get("name")}
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
        if overlaps(segment.track, segment.start, segment.end, data.get("exclusions", [])):
            updated.append(without_voice_guess(replace(segment, **originals[key])))
            uncertain += 1
            continue
        speaker = match_turn(segment, data["turns"])
        name = names.get(speaker, "")
        if name:
            updated.append(replace(segment, speaker=name, speaker_source="voice_profile"
                                   if speaker in automatic else "voice_confirmed"))
            assigned += 1
        else:
            updated.append(replace(segment, **originals[key]))
            uncertain += 1
    backup_transcript(record)
    if (record.directory / "voices.json").exists():
        from datetime import datetime
        backup = record.directory / "voice-runs" / f"review-{datetime.now():%Y%m%d-%H%M%S-%f}.json"
        backup.parent.mkdir(parents=True, exist_ok=True)
        repository._write_json(backup, raw)
    repository._write_json(baseline, originals)
    # Persist names on the raw analysis, not the filtered view: restoring an
    # exclusion can recover its original turns without reconstructing the audio.
    raw["names"] = names
    raw["auto_names"] = automatic
    repository._write_json(record.directory / "voices.json", raw)
    if segments:
        repository.save_transcript(record, updated, transcript_as_markdown(record, updated))
    return assigned, uncertain


def apply_saved_voice_names(directory: Path, segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    data = load_analysis(directory)
    if not data:
        return segments
    if data.get("corrections_unavailable"):
        return [without_voice_guess(segment) for segment in segments]
    result = []
    for segment in segments:
        if overlaps(segment.track, segment.start, segment.end, data.get("exclusions", [])):
            result.append(without_voice_guess(segment))
            continue
        speaker = match_turn(segment, data["turns"])
        name = data.get("names", {}).get(speaker, "")
        automatic = data.get("mode") == "live" or speaker in data.get("auto_names", {})
        source = "voice_profile" if automatic else "voice_confirmed"
        result.append(replace(segment, speaker=name, speaker_source=source)
                      if name and segment.speaker_source != "manual" else segment)
    return result


def _merge_groups(target, source):
    import numpy as np
    weights = (target["seconds"], source["seconds"])
    centre = np.average([target["embedding"], source["embedding"]], axis=0,
                        weights=weights if sum(weights) > 0 else None)
    target["embedding"] = (centre / max(float(np.linalg.norm(centre)), 1e-8)).tolist()
    target["seconds"] += source["seconds"]
    target["tracks"] = sorted(set(target["tracks"]) | set(source["tracks"]))
    target["samples"] = (target["samples"] + source["samples"])[:4]
    target["reference_vectors"] = (target.get("reference_vectors", [])
                                   + source.get("reference_vectors", []))[:4]


def resolve_known_voices(data: dict, database, threshold=0.55, merge=True) -> dict:
    """Name groups matching enrolled voices and merge groups of the same profile (e.g. one
    person split between MIC and Teams audio). Never enrols: names stay automatic until the
    user confirms them. Groups are renumbered by talk time; any earlier names are dropped."""
    profiles = database.list_profiles()
    groups = [group for group in data.get("groups", []) if group.get("kind") != "incerto"]
    matches = {}
    for group in groups:
        if group.get("embedding") and not group.get("needs_reanalysis"):
            group["candidates"] = database.rank(group["embedding"], profiles)[:3]
            match = database.match_group(group, threshold, profiles=profiles)
            if match:
                matches[group["speaker"]] = match
    renamed = {}
    if merge:
        owners = {}
        for group in sorted(groups, key=lambda item: item["seconds"], reverse=True):
            match = matches.get(group["speaker"])
            owner = owners.get(match["id"]) if match else None
            if owner is None:
                if match:
                    owners[match["id"]] = group
                continue
            _merge_groups(owner, group)
            renamed[group["speaker"]] = owner["speaker"]
            group["merged"] = True
        groups = [group for group in groups if not group.pop("merged", False)]
    expected = int(data.get("expected") or 0)
    groups.sort(key=lambda item: -item["seconds"])
    if expected:
        # A merge can free a person slot: the next voice by talk time takes it.
        for index, group in enumerate(groups):
            group["kind"] = "persona" if index < expected else "altra"
    keys = {group["speaker"]: f"Voce {index + 1}" for index, group in enumerate(groups)}
    keys.update({old: keys[new] for old, new in renamed.items()})
    names, automatic = {}, {}
    for group in groups:
        old = group["speaker"]
        group["speaker"] = keys[old]
        if old in matches:
            match = matches[old]
            names[keys[old]] = match["name"]
            automatic[keys[old]] = {"name": match["name"], "score": round(match["score"], 3),
                                    "profile_id": match["id"]}
    for turn in data.get("turns", []):
        turn["speaker"] = keys.get(turn["speaker"], turn["speaker"])
    uncertain = [group for group in data.get("groups", []) if group.get("kind") == "incerto"]
    data.update(groups=groups + uncertain, names=names, auto_names=automatic,
                profiles_resolved=True)
    return data
