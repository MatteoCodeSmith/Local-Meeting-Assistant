"""Start only the local LM Studio service; never load a model or send a prompt."""
from pathlib import Path
import shutil
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QProcess, QTimer, Signal


def server_command(config):
    url = urlsplit(config.lm_base_url)
    if (url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost")
            or url.username is not None or url.password is not None):
        raise ValueError("Avvio automatico consentito solo su http://127.0.0.1 o localhost.")
    port = url.port if url.port is not None else 80
    if not 1 <= port <= 65535:
        raise ValueError("Porta del server locale non valida.")
    if config.lms_executable:
        executable = Path(config.lms_executable).expanduser()
        if not executable.is_absolute() or not executable.is_file() or executable.suffix.lower() != ".exe":
            raise ValueError("Imposta lms_executable sul percorso assoluto di lms.exe.")
    else:
        candidates = [Path.home() / ".lmstudio/bin/lms.exe"]
        discovered = shutil.which("lms.exe")
        if discovered:
            candidates.append(Path(discovered))
        executable = next((path for path in candidates if path.is_file()), None)
        if executable is None:
            raise ValueError("lms.exe non trovato: installa/apri LM Studio una volta, oppure imposta lms_executable.")
    return str(executable), ["server", "start", "--port", str(port), "--bind", "127.0.0.1"]


class LMStudioStartup(QObject):
    status_changed = Signal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.status = "LM Studio: avvio non richiesto"
        self.process = QProcess(self)
        # Do not capture CLI output: diagnostics must not expose private server data.
        self.process.setStandardOutputFile(QProcess.nullDevice())
        self.process.setStandardErrorFile(QProcess.nullDevice())
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(60_000)
        self.timer.timeout.connect(self._timeout)
        self._cancelled = False

    def _status(self, text):
        self.status = text
        self.status_changed.emit(text)

    def start(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            return
        try:
            executable, arguments = server_command(self.config)
        except ValueError as exc:
            self._status("LM Studio: " + str(exc))
            return
        self._cancelled = False
        self._status("LM Studio: avvio del server locale…")
        self.timer.start()
        self.process.start(executable, arguments)

    def _finished(self, code, _exit_status):
        self.timer.stop()
        if not self._cancelled:
            self._status("LM Studio: server avviato (o già attivo)" if code == 0 else
                         "LM Studio: avvio fallito; controlla il server nell'app LM Studio")

    def _error(self, _error):
        self.timer.stop()
        if not self._cancelled:
            self._status("LM Studio: impossibile avviare il comando lms; controlla installazione e percorso")

    def _timeout(self):
        self.stop()
        self._status("LM Studio: avvio scaduto; controlla il server nell'app LM Studio")

    def stop(self):
        self._cancelled = True
        self.timer.stop()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()  # Only our pending CLI, never an existing server/model.
