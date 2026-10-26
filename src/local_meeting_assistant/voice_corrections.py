"""Reversible, timestamp-based exclusions. Never delete audio or transcript text."""
import math
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

REASONS = ("Risata", "Rumore", "Musica", "Altro non parlato")


class VoiceErrorDatabase:
    def __init__(self, path):
        self.path = Path(path)

    @classmethod
    def for_session(cls, directory):
        return cls(Path(directory).parent.parent / "voice-errors.sqlite3")

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        try:
            db.row_factory = sqlite3.Row
            db.execute("CREATE TABLE IF NOT EXISTS exclusions (id TEXT PRIMARY KEY, "
                       "session TEXT NOT NULL, track TEXT NOT NULL, start REAL NOT NULL, "
                       "end REAL NOT NULL, reason TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)")
            with db:
                yield db
        finally:
            db.close()

    def list(self, directory):
        if not self.path.exists():
            return []
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM exclusions WHERE session=? AND active=1 ORDER BY track,start",
                (Path(directory).name,))]

    def add(self, directory, spans, reason):
        if reason not in REASONS or not spans:
            raise ValueError("Seleziona intervalli validi e un motivo di esclusione.")
        rows = []
        for span in spans:
            start, end = float(span["start"]), float(span["end"])
            if (span["track"] not in ("mic", "system") or not math.isfinite(start)
                    or not math.isfinite(end) or start < 0 or end <= start):
                raise ValueError("Intervallo di esclusione non valido.")
            rows.append((uuid.uuid4().hex, Path(directory).name, span["track"], start, end, reason))
        with self._connect() as db:
            db.executemany("INSERT INTO exclusions(id,session,track,start,end,reason) VALUES(?,?,?,?,?,?)", rows)

    def restore(self, directory, identity):
        with self._connect() as db:
            db.execute("UPDATE exclusions SET active=0 WHERE id=? AND session=?",
                       (identity, Path(directory).name))


def overlaps(track, start, end, exclusions):
    return any(item["track"] == track and start < item["end"] and end > item["start"]
               for item in exclusions)


def allowed_spans(track, start, end, exclusions):
    """Subtract intervals without concatenating audio or shifting any timestamps."""
    parts = [(start, end)]
    for item in exclusions:
        if item["track"] != track:
            continue
        updated = []
        for left, right in parts:
            if item["end"] <= left or item["start"] >= right:
                updated.append((left, right))
            else:
                if left < item["start"]:
                    updated.append((left, item["start"]))
                if right > item["end"]:
                    updated.append((item["end"], right))
        parts = updated
    return parts


def filtered_analysis(data, exclusions):
    if not data:
        return data
    # Existing embeddings containing even part of an excluded interval are unsafe
    # to enroll. Recompute them from allowed audio on the next explicit analysis.
    turns = data.get("turns", [])
    removed = {turn["speaker"] for turn in turns if overlaps(
        turn["track"], turn["start"], turn["end"], exclusions)}
    kept = [turn for turn in turns if not overlaps(
        turn["track"], turn["start"], turn["end"], exclusions)]
    groups = []
    for original in data.get("groups", []):
        own = [turn for turn in kept if turn["speaker"] == original["speaker"]]
        if not own:
            continue
        group = dict(original)
        if group["speaker"] in removed:
            group.pop("embedding", None)
            group.pop("reference_vectors", None)
            group["needs_reanalysis"] = True
            group["samples"] = [sample for sample in group.get("samples", []) if not overlaps(
                sample["track"], sample["start"], sample["end"], exclusions)]
        group["seconds"] = sum(turn["end"] - turn["start"] for turn in own)
        groups.append(group)
    return {**data, "turns": kept, "groups": groups, "exclusions": exclusions}
