from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

LOGGER = logging.getLogger(__name__)
TEAMS_EXECUTABLES = {"ms-teams.exe", "teams.exe"}
LEAVE_LABELS = {
    "leave",
    "leave call",
    "hang up",
    "abbandona",
    "abbandona chiamata",
    "riaggancia",
    "termina chiamata",
}


class TeamsCallProbe:
    """Best-effort Teams call probe using audio sessions plus the accessibility tree."""

    def __init__(self) -> None:
        self._last_uia_check = 0.0
        self._last_uia_result = False

    def is_call_active(self) -> bool:
        pids = self._teams_pids()
        if not pids:
            self._last_uia_result = False
            return False
        now = time.monotonic()
        if now - self._last_uia_check >= 4.0:
            self._last_uia_check = now
            self._last_uia_result = self._has_call_controls(pids)
        return self._last_uia_result or self._has_active_audio_session(pids)

    @staticmethod
    def _teams_pids() -> set[int]:
        try:
            import psutil

            return {
                process.info["pid"]
                for process in psutil.process_iter(["pid", "name"])
                if (process.info.get("name") or "").lower() in TEAMS_EXECUTABLES
            }
        except Exception:
            LOGGER.exception("Could not enumerate Teams processes")
            return set()

    @staticmethod
    def _has_active_audio_session(pids: set[int]) -> bool:
        try:
            from pycaw.pycaw import AudioUtilities

            for session in AudioUtilities.GetAllSessions():
                process = getattr(session, "Process", None)
                if process is None or process.pid not in pids:
                    continue
                # Windows AudioSessionStateActive == 1.
                if int(getattr(session, "State", 0)) == 1:
                    return True
        except Exception:
            LOGGER.debug("Teams audio session probe unavailable", exc_info=True)
        return False

    @staticmethod
    def _has_call_controls(pids: set[int]) -> bool:
        try:
            from pywinauto import Desktop

            desktop = Desktop(backend="uia")
            for window in desktop.windows(visible_only=False):
                try:
                    if window.process_id() not in pids:
                        continue
                    for button in window.descendants(control_type="Button"):
                        label = (button.window_text() or "").strip().lower()
                        if label in LEAVE_LABELS:
                            return True
                except Exception:
                    continue
        except Exception:
            LOGGER.debug("Teams UI Automation probe unavailable", exc_info=True)
        return False


class TeamsDetector(threading.Thread):
    def __init__(
        self,
        *,
        poll_seconds: float,
        start_confirmations: int,
        end_confirmations: int,
        on_changed: Callable[[bool], None],
    ) -> None:
        super().__init__(name="teams-detector", daemon=True)
        self.poll_seconds = poll_seconds
        self.start_confirmations = start_confirmations
        self.end_confirmations = end_confirmations
        self.on_changed = on_changed
        self.probe = TeamsCallProbe()
        self.stop_event = threading.Event()
        self._active = False

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        try:
            import comtypes

            comtypes.CoInitialize()
        except Exception:
            comtypes = None
        positive = 0
        negative = 0
        try:
            while not self.stop_event.wait(self.poll_seconds):
                observed = self.probe.is_call_active()
                positive = positive + 1 if observed else 0
                negative = negative + 1 if not observed else 0
                if not self._active and positive >= self.start_confirmations:
                    self._active = True
                    self.on_changed(True)
                elif self._active and negative >= self.end_confirmations:
                    self._active = False
                    self.on_changed(False)
        finally:
            if comtypes is not None:
                try:
                    comtypes.CoUninitialize()
                except Exception:
                    pass

