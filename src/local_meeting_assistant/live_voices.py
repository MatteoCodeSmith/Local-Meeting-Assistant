"""Bounded asynchronous live analysis, independent of Whisper and the capture loop."""
import base64
import json
import queue
import time

import numpy as np
from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from .voice_database import normalized
from .voice_jobs import PROJECT_ROOT, VoiceJobs


class LiveVoices(QObject):
    status = Signal(bool, str)
    saved = Signal(object)
    attributed = Signal(str, str, object)

    def __init__(self, repository, database, parent=None):
        super().__init__(parent)
        self.repository, self.database = repository, database
        self.pending = queue.Queue(maxsize=4)
        self.sessions = {}
        self._ready, self._working, self._closing = False, False, False
        self._buffer = b""
        self._dropped = 0
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._pump)
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.readyReadStandardError.connect(lambda: self.process.readAllStandardError())
        self.process.errorOccurred.connect(lambda _error: self._failed())
        self.process.finished.connect(lambda _code, _status: self._failed())

    def start(self):
        if not VoiceJobs.available():
            self.status.emit(False, "Voci non installate: usa scripts/install-voices.ps1")
            return
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONPATH", str(PROJECT_ROOT / "src"))
        self.process.setProcessEnvironment(environment)
        python, checkpoint = VoiceJobs.paths()
        self.process.start(str(python), ["-m", "local_meeting_assistant.live_voice_worker", str(checkpoint)])
        self.status.emit(False, "Caricamento voci locale · CPU")
        self.timer.start()

    def begin(self, record):
        self.sessions[record.session_id] = {"record": record, "closed": False, "last_save": 0,
            "data": {"version": 1, "mode": "live", "groups": [], "turns": [], "names": {},
                     "warning": "Gruppi vocali stimati. I nomi della rubrica sono corrispondenze automatiche da verificare."}}

    def is_pending(self, session_id):
        return session_id in self.sessions and self.sessions[session_id]["closed"] and self._ready

    def offer(self, session_id, track, pcm, rate, offset):
        # Called by capture threads: no inference, disk I/O, waiting or Qt process calls here.
        try:
            self.pending.put_nowait((session_id, track, pcm, rate, offset))
        except queue.Full:
            self._dropped += 1

    def end(self, record, cancelled=False):
        entry = self.sessions.get(record.session_id)
        if entry:
            if cancelled:
                self.sessions.pop(record.session_id, None)
            else:
                entry["closed"] = True
                self._save(entry)

    def _pump(self):
        if not self._ready or self._working:
            return
        try:
            session_id, track, pcm, rate, offset = self.pending.get_nowait()
        except queue.Empty:
            # All closed sessions have now been flushed; keep no audio/fingerprints in RAM.
            for key, entry in list(self.sessions.items()):
                if entry["closed"]:
                    self.sessions.pop(key, None)
                    self._save(entry)
            return
        if session_id not in self.sessions:
            return
        mono = np.asarray(pcm, dtype=np.float32)
        if mono.ndim == 2:
            mono = mono.mean(axis=1)
        payload = {"session": session_id, "track": track, "rate": rate, "offset": offset,
                   "pcm": base64.b64encode(mono.tobytes()).decode("ascii")}
        self._working = True
        self.process.write((json.dumps(payload) + "\n").encode("utf-8"))
        if self._dropped:
            self.status.emit(True, "Voci in ritardo: alcuni campioni saltati. Audio e Whisper restano integri.")
            self._dropped = 0

    def _read(self):
        self._buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if payload.get("ready"):
                self._ready = True
                self.status.emit(True, "Voci pronte · CPU · rubrica locale")
            elif payload.get("startup_error"):
                self._failed()
            else:
                self._working = False
                entry = self.sessions.get(payload.get("session"))
                if entry:
                    try:
                        self._apply(entry, payload["track"], payload.get("turns", []))
                    except Exception:
                        self.status.emit(False, "Campione vocale non elaborato; Whisper continua.")
        self._buffer = self._buffer[-65536:]

    def _apply(self, entry, track, turns):
        data = entry["data"]
        changed = []
        for turn in turns:
            vector = normalized(turn["embedding"])
            match = self.database.match(vector)
            if match:
                key = "Profilo " + match["id"]
                data["names"][key] = match["name"]
                self.status.emit(True, f"Voce: {match['name']} · corrispondenza da verificare")
            else:
                candidates = [(float(np.dot(vector, normalized(group["embedding"]))), group["speaker"])
                              for group in data["groups"] if group["speaker"].startswith("Voce ")]
                best = max(candidates, default=(0, ""))
                key = best[1] if best[0] >= 0.75 else f"Voce {len(data['groups']) + 1}"
                self.status.emit(True, "Voce non riconosciuta · da verificare nell'archivio")
            group = next((group for group in data["groups"] if group["speaker"] == key), None)
            if group is None:
                group = {"speaker": key, "tracks": [], "seconds": 0., "samples": [], "embedding": vector.tolist()}
                data["groups"].append(group)
            span = {"track": track, "start": turn["start"], "end": turn["end"], "speaker": key}
            data["turns"].append(span)
            changed.append(span)
            group["seconds"] += turn["end"] - turn["start"]
            if track not in group["tracks"]:
                group["tracks"].append(track)
            if len(group["samples"]) < 3:
                group["samples"].append(span)
        # Notify immediately; the periodic disk snapshot is not a UI/attribution barrier.
        self.attributed.emit(entry["record"].session_id, track, changed)
        if entry["closed"] or time.monotonic() - entry["last_save"] >= 15:
            self._save(entry)

    def _save(self, entry):
        try:
            self.repository._write_json(entry["record"].directory / "live-voices.json", entry["data"])
            entry["last_save"] = time.monotonic()
            self.saved.emit(entry["record"])
        except OSError:
            self.status.emit(False, "Risultati vocali non salvati; trascrizione Whisper indipendente.")

    def _failed(self):
        self._ready, self._working = False, False
        self.timer.stop()
        for entry in self.sessions.values():
            self._save(entry)
        self.sessions.clear()
        if not self._closing:
            self.status.emit(False, "Motore voci non disponibile; Whisper continua. Riavvia BIONIC per riprovare.")

    def close(self):
        self._closing = True
        self.timer.stop()
        for entry in self.sessions.values():
            self._save(entry)
        self.process.kill()
        self.process.waitForFinished(1500)
