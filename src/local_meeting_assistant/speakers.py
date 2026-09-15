"""Read-only, best-effort Teams active-speaker evidence, never voice identification.

UIA names are hints, not an official Teams media API. Unsupported UI, stale
observations, overlapping speakers and ambiguous segments must remain unnamed.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .detector import LEAVE_LABELS, TeamsCallProbe
from .domain import TranscriptSegment

LOGGER = logging.getLogger(__name__)
UNKNOWN_REMOTE = "Partecipante remoto non identificato"
_LABELS = (
    r"(.+?)(?:,|\s[-–—])\s*(?:is speaking|speaking|sta parlando)",
    r"(.+?)\s+(?:is speaking|sta parlando)",
    r"(?:Speaking|Parla|Sta parlando)\s*:\s*(.+)",
)


def speaker_from_label(label: str) -> str | None:
    """Accept explicit status only; a roster, unmuted mic or selected tile is not evidence."""
    for pattern in _LABELS:
        match = re.fullmatch(pattern, label.strip(), re.IGNORECASE)
        if not match:
            continue
        name = match[1].strip()
        if (
            2 <= len(name) <= 100
            and "\n" not in name
            and not any(char in name for char in ":;[]")
            and name.casefold()
            not in {"you", "tu", "io", "me", "speaker", "unknown", "partecipante", "sconosciuto"}
        ):
            return name
    return None


def probe_active_speakers() -> tuple[tuple[str, ...], str]:
    from pywinauto import Desktop

    pids = TeamsCallProbe._teams_pids()
    if not pids:
        return (), "Teams non aperto"
    calls = []
    for window in Desktop(backend="uia").windows(visible_only=True):
        if window.process_id() not in pids:
            continue
        if any(
            (button.window_text() or "").strip().casefold() in LEAVE_LABELS
            for button in window.descendants(control_type="Button")
        ):
            calls.append(window)
    if len(calls) != 1:
        return (), "Nomi non disponibili: nessuna finestra di chiamata univoca"
    names = set()
    for element in calls[0].descendants():
        # Do not interpret chat messages or caption text as an active-speaker signal.
        if element.element_info.control_type not in {"Group", "Pane", "ListItem", "Button"}:
            continue
        if element.is_visible():
            name = speaker_from_label(element.window_text() or "")
            if name:
                names.add(name)
    if not names:
        return (), "Nomi non disponibili: Teams non espone un indicatore vocale compatibile"
    return tuple(sorted(names)), "Indicatore Teams: " + ", ".join(sorted(names))


class TeamsSpeakerMonitor(threading.Thread):
    def __init__(self) -> None:
        super().__init__(name="teams-speakers", daemon=True)
        self.stop_event = threading.Event()
        self.enabled = threading.Event()
        self._lock = threading.Lock()
        self._observation: tuple[float, tuple[str, ...]] = (0.0, ())
        self._generation = 0
        self.status = "In attesa di una registrazione Teams"

    def names_at(self, captured_at: float) -> tuple[str, ...]:
        with self._lock:
            timestamp, names = self._observation
        # Never reuse the last known name through missing observations/minimized Teams.
        return names if self.enabled.is_set() and 0 <= captured_at - timestamp <= 1.25 else ()

    def set_recording(self, enabled: bool) -> None:
        with self._lock:
            self._observation = (0.0, ())
            self._generation += 1
        if enabled:
            self.status = "Ricerca indicatore vocale Teams…"
            self.enabled.set()
        else:
            self.enabled.clear()
            self.status = "In attesa di una registrazione Teams"

    def run(self) -> None:
        import comtypes

        comtypes.CoInitialize()
        try:
            while not self.stop_event.wait(0.5):
                if not self.enabled.is_set():
                    continue
                before = time.monotonic()
                generation = self._generation
                try:
                    names, status = probe_active_speakers()
                except Exception:
                    LOGGER.debug("Teams speaker UIA unavailable", exc_info=True)
                    names, status = (), "Lettura nomi Teams non disponibile"
                # A slow accessibility scan is not real-time evidence.
                if time.monotonic() - before > 1.0:
                    names, status = (), "Lettura Teams troppo lenta: nomi non assegnati"
                with self._lock:
                    if generation == self._generation and self.enabled.is_set():
                        self._observation = (before, names)
                        self.status = status
        finally:
            comtypes.CoUninitialize()


@dataclass
class SpeakerSpan:
    start: float
    end: float
    names: tuple[str, ...]


class SpeakerTimeline:
    def __init__(self) -> None:
        self.spans: list[SpeakerSpan] = []

    def add(self, start: float, end: float, names: tuple[str, ...]) -> None:
        if end <= start:
            return
        if self.spans and self.spans[-1].names == names and abs(self.spans[-1].end - start) < 0.01:
            self.spans[-1].end = end
        else:
            self.spans.append(SpeakerSpan(start, end, names))

    def save(self, directory: Path) -> None:
        target = directory / "speakers.json"
        temp = target.with_suffix(".tmp")
        temp.write_text(
            json.dumps(
                {
                    "version": 1,
                    "source": "teams_uia_approximate",
                    "spans": [asdict(span) for span in self.spans],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temp.replace(target)

    @classmethod
    def load(cls, directory: Path) -> SpeakerTimeline:
        timeline = cls()
        path = directory / "speakers.json"
        if not path.exists():
            return timeline
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or data.get("source") != "teams_uia_approximate":
            raise ValueError("Formato cronologia parlanti non supportato")
        for row in data["spans"]:
            start, end = float(row["start"]), float(row["end"])
            names = row["names"]
            if (
                not isinstance(names, list)
                or not all(isinstance(name, str) for name in names)
                or start < 0
                or end <= start
                or (timeline.spans and start < timeline.spans[-1].end)
            ):
                raise ValueError("Cronologia parlanti non valida")
            timeline.add(start, end, tuple(names))
        return timeline

    def attribute(self, segment: TranscriptSegment) -> TranscriptSegment:
        if segment.track != "system":
            return segment
        duration = segment.end - segment.start
        evidence: dict[str, float] = {}
        ambiguous = False
        for span in self.spans:
            overlap = min(span.end, segment.end) - max(span.start, segment.start)
            if overlap <= 0:
                continue
            if len(span.names) > 1:
                ambiguous = True
            elif len(span.names) == 1:
                evidence[span.names[0]] = evidence.get(span.names[0], 0.0) + overlap
        if duration > 0 and not ambiguous and len(evidence) == 1:
            name, covered = next(iter(evidence.items()))
            if covered / duration >= 0.9:
                return replace(segment, speaker=name, speaker_source="teams_uia_approximate")
        return replace(segment, speaker=UNKNOWN_REMOTE, speaker_source=None)
