from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile, QStandardPaths, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from .config import AppConfig
from .controller import AssistantController
from .ui import OverlayWindow, TrayController


def _configure_logging() -> None:
    log_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "meeting-assistant.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Local Meeting Assistant")
    app.setOrganizationName("LocalMeetingAssistant")
    app.setQuitOnLastWindowClosed(False)
    _configure_logging()

    lock_path = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation))
    lock = QLockFile(str(lock_path / "local-meeting-assistant.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(
            None, "Local Meeting Assistant",
            "BIONIC è già in esecuzione.\n\n"
            "Per applicare un aggiornamento, quando non stai registrando o elaborando, "
            "scegli Esci dal menu dell'icona vicino all'orologio di Windows, "
            "poi riapri BIONIC dal collegamento sul desktop. "
            "Chiudere una finestra non termina l'app.",
        )
        return 0

    config = AppConfig.load()
    config_path = config.save()
    controller = AssistantController(config)
    overlay = OverlayWindow(controller, config)
    tray = TrayController(controller, overlay, config, config_path)
    overlay.place_top_right()
    overlay.show()
    tray.show()
    if "--appearance" in sys.argv:
        overlay.show_appearance()
    if "--recognition" in sys.argv:
        QTimer.singleShot(0, overlay.show_recognition)

    def shutdown() -> None:
        controller.shutdown()
        lock.unlock()

    app.aboutToQuit.connect(shutdown)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
