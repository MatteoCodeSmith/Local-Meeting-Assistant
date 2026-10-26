"""Explicitly enrolled local voice profiles. Never learns a person's name by guessing."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from .participants import clean_name

ENGINE = "speechbrain-ecapa-voxceleb-192-v1"


def normalized(vector):
    data = np.asarray(vector, dtype=np.float32)
    if data.shape != (192,) or not np.isfinite(data).all() or np.linalg.norm(data) < 1e-8:
        raise ValueError("Impronta vocale non valida.")
    return data / np.linalg.norm(data)


class VoiceDatabase:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.execute("CREATE TABLE IF NOT EXISTS profiles "
                           "(id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, engine TEXT NOT NULL, vectors TEXT NOT NULL)")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def list_profiles(self):
        if not self.path.exists():
            return []
        with self._connect() as db:
            return [{"id": row[0], "name": row[1], "engine": row[2], "vectors": json.loads(row[3])}
                    for row in db.execute("SELECT id,name,engine,vectors FROM profiles ORDER BY name")]

    def enroll(self, name, vector):
        name = clean_name(name)
        if not name:
            raise ValueError("Assegna e verifica il nome prima di salvarlo nella rubrica vocale.")
        vector = normalized(vector).tolist()
        with self._connect() as db:
            old = db.execute("SELECT id,vectors,engine FROM profiles WHERE name=?", (name,)).fetchone()
            identity = old[0] if old else uuid.uuid4().hex
            vectors = json.loads(old[1]) if old and old[2] == ENGINE else []
            vectors = (vectors + [vector])[-8:]
            db.execute("INSERT INTO profiles VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET engine=excluded.engine,vectors=excluded.vectors",
                       (identity, name, ENGINE, json.dumps(vectors)))
        return identity

    def delete(self, identity):
        with self._connect() as db:
            db.execute("DELETE FROM profiles WHERE id=?", (identity,))

    def rank(self, vector, profiles=None):
        """All compatible profiles by cosine similarity, best first. Metadata, not identity."""
        vector = normalized(vector)
        ranked = []
        for profile in self.list_profiles() if profiles is None else profiles:
            if profile["engine"] != ENGINE:
                continue
            score = max(float(np.dot(vector, normalized(item))) for item in profile["vectors"])
            ranked.append({"id": profile["id"], "name": profile["name"], "score": score})
        return sorted(ranked, key=lambda item: item["score"], reverse=True)

    # Defaults are the Live behaviour, kept unchanged. Archive analysis passes its own,
    # lower thresholds: ECAPA same-speaker cosine between group centres is ~0.5-0.8.
    def match(self, vector, threshold=0.80, margin=0.08, profiles=None):
        ranked = self.rank(vector, profiles)
        if (not ranked or ranked[0]["score"] < threshold
                or (len(ranked) > 1 and ranked[0]["score"] - ranked[1]["score"] < margin)):
            return None
        return ranked[0]

    def match_group(self, group, threshold=0.55, margin=0.05, profiles=None):
        """Group centre against profiles; no sample may point to a competing profile."""
        if group.get("needs_reanalysis") or not group.get("embedding"):
            return None
        profiles = self.list_profiles() if profiles is None else profiles
        match = self.match(group["embedding"], threshold, margin, profiles)
        if not match:
            return None
        for vector in group.get("reference_vectors") or []:
            competing = self.match(vector, threshold - 0.1, 0.0, profiles)
            if competing and competing["id"] != match["id"]:
                return None
        return match
