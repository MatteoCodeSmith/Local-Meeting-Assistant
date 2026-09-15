"""Qt-managed child process: private audio is only read inside the offline local worker."""
import json
import os
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class VoiceJobs(QObject):
    progress = Signal(str, int)
    finished = Signal(object, str)

    def __init__(self, repository, parent=None):
        super().__init__(parent)
        self.repository = repository
        self._process = None
        self._cancelled = False
        self.session_id = None

    @staticmethod
    def paths():
        return (PROJECT_ROOT / ".venv-voices" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"),
                PROJECT_ROOT / "models/ecapa/embedding_model.ckpt")

    @classmethod
    def available(cls):
        return all(path.is_file() for path in cls.paths())

    def start(self, record, expected=0, threshold=0.7):
        if self._process is not None:
            raise ValueError("Analisi vocale già in corso.")
        if not self.available():
            raise ValueError("Componente voci mancante: esegui scripts/install-voices.ps1.")
        python, checkpoint = self.paths()
        run = record.directory / "voice-runs" / uuid.uuid4().hex
        run.mkdir(parents=True)
        output = run / "analysis.json"
        process = QProcess(self)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONPATH", str(PROJECT_ROOT / "src"))
        for key, value in {"HF_HUB_OFFLINE": "1", "CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "2",
                           "MKL_NUM_THREADS": "2", "PYTHONUNBUFFERED": "1"}.items():
            environment.insert(key, value)
        process.setProcessEnvironment(environment)
        process.setWorkingDirectory(str(PROJECT_ROOT))
        self._process, self._cancelled = process, False
        self.session_id = record.session_id
        self._buffer = b""
        self._error_type = ""
        process.readyReadStandardOutput.connect(lambda: self._read(process, record.session_id))
        # Never accumulate audio-related stderr or show it in the archive.
        process.readyReadStandardError.connect(lambda: process.readAllStandardError())
        process.finished.connect(lambda code, status: self._finish(process, record, output, code))
        process.errorOccurred.connect(lambda error: self._finish(process, record, output, -1)
                                     if error == QProcess.ProcessError.FailedToStart else None)
        process.start(str(python), ["-m", "local_meeting_assistant.voice_worker", "--directory", str(record.directory),
                                   "--checkpoint", str(checkpoint), "--output", str(output),
                                   "--expected", str(expected), "--threshold", str(threshold)])

    def _read(self, process, session_id):
        self._buffer += bytes(process.readAllStandardOutput())
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            try:
                value = json.loads(line)
                if "progress" in value:
                    self.progress.emit(session_id, int(value["progress"]))
                if "error" in value:
                    self._error_type = str(value["error"])[:80]
            except (ValueError, TypeError):
                pass
        self._buffer = self._buffer[-4096:]

    def _finish(self, process, record, output, code):
        if self._process is not process:
            return
        self._read(process, record.session_id)
        message = ""
        try:
            if self._cancelled:
                message = "Analisi voci interrotta. Risultati precedenti conservati; Whisper ha priorità."
            elif code != 0:
                message = f"Analisi voci non completata ({self._error_type or 'errore del motore'}). " \
                          "Verifica il componente con scripts/install-voices.ps1. Nessuna trascrizione modificata."
            else:
                data = json.loads(output.read_text(encoding="utf-8"))
                previous = record.directory / "voices.json"
                if previous.exists():
                    self.repository._write_json(output.parent / "previous-review.json",
                                                json.loads(previous.read_text(encoding="utf-8")))
                self.repository._write_json(previous, data)
        except (OSError, ValueError):
            message = "Risultato vocale non salvato. Nessuna trascrizione modificata."
        self._process = None
        self.session_id = None
        process.deleteLater()
        self.finished.emit(record, message)

    def cancel(self):
        if self._process is not None:
            self._cancelled = True
            self._process.kill()

    def close(self):
        process = self._process
        self.cancel()
        if process is not None:
            process.waitForFinished(1500)
