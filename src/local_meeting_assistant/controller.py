from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from .config import AppConfig
from .detector import TeamsDetector
from .domain import SessionRecord, SessionSource, SessionStatus
from .recorder import AudioRecorder, RecorderCallbacks
from .storage import SessionRepository
from .transcription import LocalProcessingPipeline, PipelineCallbacks
from .speakers import SpeakerTimeline, TeamsSpeakerMonitor
from .participants import Participants, backup_transcript, relabel_transcript, segment_key

LOGGER = logging.getLogger(__name__)


class AssistantController(QObject):
    recording_changed = Signal(bool, str)
    track_changed = Signal(str, bool)
    level_changed = Signal(str, float)
    ai_changed = Signal(bool, str)
    message = Signal(str)
    error = Signal(str)
    session_finished = Signal(object, object)
    teams_changed = Signal(bool)
    voices_finished = Signal(str)
    voices_progress = Signal(str, int)
    voice_changed = Signal(bool, str)
    live_speaker_changed = Signal(str, str)
    live_transcript_changed = Signal(str, object)
    live_transcript_reset = Signal(str)

    _teams_observed = Signal(bool)
    _pipeline_status = Signal(str, str)
    _pipeline_partial = Signal(str, object)
    _pipeline_finished = Signal(object, object)
    _pipeline_error = Signal(str, str)

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self.repository = SessionRepository(config.recordings_path)
        self._lock = threading.RLock()
        self._current: SessionRecord | None = None
        self._teams_active = False
        self._skip_current_teams_call = False
        self._processing: set[str] = set()
        self._live_record = None
        self._live_originals = {}
        self._live_display = {}
        self._live_voice_data = {}
        self._live_participants = Participants()
        from .voice_jobs import VoiceJobs
        self.voices = VoiceJobs(self.repository, self)
        self.voices.finished.connect(self._on_voices_finished)
        self.voices.progress.connect(self.voices_progress)
        from .voice_database import VoiceDatabase
        from .live_voices import LiveVoices
        self.voice_database = VoiceDatabase(config.recordings_path.parent / "voice-profiles.sqlite3")
        self.live_voices = LiveVoices(self.repository, self.voice_database, self)
        self.live_voices.status.connect(self.voice_changed)
        self.live_voices.saved.connect(self._on_live_voices_saved)
        self.live_voices.attributed.connect(self._on_voice_attributed)
        if config.live_voice_recognition:
            QTimer.singleShot(0, self.live_voices.start)
        self.speaker_monitor = TeamsSpeakerMonitor()
        self.speaker_monitor.start()
        self._speaker_timeline = SpeakerTimeline()

        recorder_callbacks = RecorderCallbacks(
            on_level=lambda track, level: self.level_changed.emit(track, level),
            on_chunk=self._on_chunk_from_capture_thread,
            on_error=lambda track, detail: self.error.emit(f"{track}: {detail}"),
            on_track_state=lambda track, active: self.track_changed.emit(track, active),
            on_device=lambda track, name: self.message.emit(
                f"Microfono: {name}" if track == "mic" else name
            ),
            on_audio_span=self._on_audio_span,
            on_voice_audio=self._on_voice_audio if config.live_voice_recognition else None,
        )
        self.recorder = AudioRecorder(recorder_callbacks)

        pipeline_callbacks = PipelineCallbacks(
            on_status=lambda session_id, status: self._pipeline_status.emit(session_id, status),
            on_partial=lambda session_id, segment: self._pipeline_partial.emit(session_id, segment),
            on_finished=lambda record, path: self._pipeline_finished.emit(record, path),
            on_error=lambda session_id, detail: self._pipeline_error.emit(session_id, detail),
        )
        self.pipeline = LocalProcessingPipeline(config, self.repository, pipeline_callbacks)

        self._teams_observed.connect(self._on_teams_observed)
        self._pipeline_status.connect(self._on_pipeline_status)
        self._pipeline_finished.connect(self._on_pipeline_finished)
        self._pipeline_error.connect(self._on_pipeline_error)
        self._pipeline_partial.connect(self._on_live_partial)

        self.detector: TeamsDetector | None = None
        if config.auto_detect_teams:
            self.detector = TeamsDetector(
                poll_seconds=config.teams_poll_seconds,
                start_confirmations=config.teams_start_confirmations,
                end_confirmations=config.teams_end_confirmations,
                on_changed=lambda active: self._teams_observed.emit(active),
            )
            self.detector.start()

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._current is not None

    @Slot()
    def start_manual(self) -> None:
        self.start_recording(SessionSource.MANUAL)

    def start_recording(self, source: SessionSource) -> bool:
        with self._lock:
            if self._current is not None:
                self.message.emit("Una registrazione e gia in corso.")
                return False
            title = "Meeting Teams" if source is SessionSource.TEAMS else "Conversazione"
            record = self.repository.create(source, title)
            Participants(local_name=self.config.local_display_name,
                         microphone_is_me=source is SessionSource.TEAMS).save(record.directory)
            self._begin_live_transcript(record, Participants(
                local_name=self.config.local_display_name, microphone_is_me=source is SessionSource.TEAMS))
            self._current = record
            if hasattr(self, "voices"):
                self.voices.cancel()
            if hasattr(self, "live_voices") and self.config.live_voice_recognition:
                self.live_voices.begin(record)
            self.pipeline.set_recording(True)
            self._speaker_timeline = SpeakerTimeline()
            self.speaker_monitor.set_recording(
                source is SessionSource.TEAMS and self.config.teams_speaker_names
            )
        try:
            tracks = self.recorder.start(
                record.directory,
                sample_rate=self.config.sample_rate,
                block_seconds=self.config.capture_block_seconds,
                chunk_seconds=self.config.transcription_chunk_seconds,
                microphone=self.config.capture_microphone,
                system_audio=self.config.capture_system_audio,
                microphone_name=self.config.microphone_name,
                speaker_name=self.config.speaker_name,
                follow_default_microphone=self.config.follow_default_microphone,
                device_poll_seconds=self.config.audio_device_poll_seconds,
            )
        except Exception as exc:
            with self._lock:
                self._current = None
                if hasattr(self, "live_voices"):
                    self.live_voices.end(record, cancelled=True)
                self.pipeline.set_recording(False)
                self.speaker_monitor.set_recording(False)
            record.status = SessionStatus.ERROR
            record.error = str(exc)
            record.ended_at = datetime.now(UTC)
            self.repository.save_record(record)
            self.error.emit(f"Registrazione non avviata: {exc}")
            return False
        self.recording_changed.emit(True, source.value)
        self.message.emit(
            f"Registrazione {'Teams' if source is SessionSource.TEAMS else 'manuale'} avviata "
            f"({', '.join(tracks)})."
        )
        return True

    def list_sessions(self) -> list[SessionRecord]:
        return self.repository.list_records()

    @property
    def current_session(self) -> SessionRecord | None:
        with self._lock:
            return self._current

    def save_participants(self, record: SessionRecord, participants: Participants) -> bool:
        with self._lock:
            live = self._current is not None and self._current.session_id == record.session_id
            if not live and record.session_id in self._processing:
                self.message.emit("Attendi la fine dell'elaborazione prima di cambiare i nomi.")
                return False
            backup_transcript(record)
            participants.save(record.directory)
            if getattr(self, "_live_record", None) is record:
                self._live_participants = participants
                for segment in self._live_originals.values():
                    self._publish_live_segment(segment)
            if not live:
                relabel_transcript(record, participants, self.repository)
                if (record.directory / "recap.md").exists():
                    record.error = "Nomi aggiornati: rigenera il recap per applicarli anche al riepilogo."
                    self.repository.save_record(record)
        self.message.emit("Nomi salvati per questa sessione. Il recap esistente non è stato riscritto.")
        return True

    def _on_audio_span(self, track: str, start: float, end: float, captured_at: float) -> None:
        if track != "system":
            return
        with self._lock:
            if self._current and self._current.source is SessionSource.TEAMS:
                self._speaker_timeline.add(start, end, self.speaker_monitor.names_at(captured_at))

    def retranscribe_session(self, record: SessionRecord) -> bool:
        return self._queue_archive_job(record, recap_only=False)

    def generate_recap(self, record: SessionRecord) -> bool:
        return self._queue_archive_job(record, recap_only=True)

    def analyse_voices(self, record, expected=0, threshold=0.7):
        with self._lock:
            if self._current is not None or self._processing or self.is_session_busy(record):
                self.message.emit("Attendi la fine della registrazione o elaborazione. Whisper ha priorità.")
                return False
            try:
                self.repository._editable_archive_record(record)
                if not any((record.directory / f"{track}.flac").is_file() for track in ("mic", "system")):
                    raise ValueError("Audio originale non disponibile.")
                self._processing.add(record.session_id)
                self.voices.start(record, expected, threshold)
            except (OSError, ValueError) as exc:
                self._processing.discard(record.session_id)
                self.error.emit(str(exc))
                return False
        self.ai_changed.emit(True, "Analisi voci locale · CPU")
        return True

    def save_voice_names(self, record, names):
        with self._lock:
            if self.is_session_busy(record):
                raise ValueError("Attendi la fine dell'analisi prima di applicare i nomi.")
            self.repository._editable_archive_record(record)
            from .voices import apply_voice_names
            counts = apply_voice_names(record, names, self.repository)
        self.message.emit("Nomi vocali applicati. Il recap resta invariato: rigeneralo solo se lo desideri.")
        return counts

    def _on_voices_finished(self, record, detail):
        self._processing.discard(record.session_id)
        self.ai_changed.emit(bool(self._processing), "Analisi voci terminata")
        self.message.emit(detail or "Voci analizzate: apri Analizza voci nell'archivio per ascoltarle e assegnare i nomi.")
        self.voices_finished.emit(record.session_id)

    def is_session_busy(self, record: SessionRecord) -> bool:
        with self._lock:
            return (record.session_id in self._processing
                    or (self._current is not None and self._current.session_id == record.session_id)
                    or (hasattr(self, "live_voices") and self.live_voices.is_pending(record.session_id)))

    def rename_session(self, record: SessionRecord, title: str) -> bool:
        with self._lock:
            if self.is_session_busy(record):
                self.message.emit("Sessione in uso: attendi prima di rinominarla.")
                return False
            try:
                self.repository.rename(record, title)
            except (OSError, ValueError) as exc:
                self.error.emit(f"Nome non aggiornato: {exc}")
                return False
        self.message.emit("Nome del meeting aggiornato.")
        return True

    def delete_session(self, record: SessionRecord) -> bool:
        with self._lock:
            if self.is_session_busy(record):
                self.message.emit("Sessione in uso: attendi prima di eliminarla.")
                return False
            try:
                self.repository.trash(record)
            except Exception as exc:
                # Recycle Bin backends also raise platform-specific exceptions.
                self.error.emit(f"Spostamento nel Cestino non riuscito; nessuna eliminazione permanente: {exc}")
                return False
        self.message.emit("Meeting spostato nel Cestino di Windows.")
        return True

    def _queue_archive_job(self, record: SessionRecord, *, recap_only: bool) -> bool:
        with self._lock:
            if self.is_session_busy(record):
                self.message.emit("Sessione ancora in elaborazione: attendi anche la fine dell'analisi vocale.")
                return False
            if self._current is not None:
                self.message.emit("Registrazione in corso: Whisper ha priorità. Riprova dall'archivio dopo il meeting.")
                return False
            if self._processing:
                self.message.emit("Attendi che le trascrizioni/elaborazioni in corso siano terminate.")
                return False
            if self._current and self._current.session_id == record.session_id:
                self.message.emit("Ferma prima la registrazione corrente.")
                return False
            if record.session_id in self._processing:
                self.message.emit("Questa sessione è già in elaborazione.")
                return False
            self._processing.add(record.session_id)
        action = "Recap in coda" if recap_only else "Trascrizione completa in coda"
        self.ai_changed.emit(True, action)
        self.message.emit(action)
        if recap_only:
            self.pipeline.recap(record)
        else:
            self.pipeline.retranscribe(record)
        return True

    @Slot()
    def stop_and_process(self) -> None:
        with self._lock:
            record = self._current
        if record is None:
            return
        try:
            self.recorder.stop()
        except Exception as exc:
            self.error.emit(str(exc))
        with self._lock:
            self.speaker_monitor.set_recording(False)
            if record.source is SessionSource.TEAMS:
                try:
                    self._speaker_timeline.save(record.directory)
                except OSError as exc:
                    self.error.emit(f"Cronologia parlanti non salvata: {exc}")
            if self._current is record:
                self._current = None
                self.pipeline.set_recording(False)
            if self._teams_active and record.source is SessionSource.TEAMS:
                self._skip_current_teams_call = True
            self._processing.add(record.session_id)
        record.ended_at = datetime.now(UTC)
        record.status = SessionStatus.TRANSCRIBING
        self.repository.save_record(record)
        self.recording_changed.emit(False, record.source.value)
        self.ai_changed.emit(True, "Trascrizione in coda")
        self.pipeline.finish(record)
        if hasattr(self, "live_voices"):
            self.live_voices.end(record)

    @Slot()
    def cancel_and_delete(self) -> None:
        with self._lock:
            record = self._current
        if record is None:
            return
        try:
            self.recorder.stop()
        except Exception as exc:
            self.error.emit(str(exc))
        with self._lock:
            self.speaker_monitor.set_recording(False)
            if self._current is record:
                self._current = None
                self.pipeline.set_recording(False)
            if self._teams_active and record.source is SessionSource.TEAMS:
                self._skip_current_teams_call = True
        self.recording_changed.emit(False, record.source.value)
        if hasattr(self, "live_voices"):
            self.live_voices.end(record, cancelled=True)
        if getattr(self, "_live_record", None) is record:
            self._begin_live_transcript(None, Participants())
        self.pipeline.cancel(record)
        self.message.emit("Sessione annullata: eliminazione in corso.")

    def shutdown(self) -> None:
        if hasattr(self, "voices"):
            self.voices.close()
        self.speaker_monitor.stop_event.set()
        self.speaker_monitor.join(2.0)
        if self.detector:
            self.detector.stop()
            self.detector.join(3.0)
        if self.recorder.running:
            self.stop_and_process()
        if hasattr(self, "live_voices"):
            self.live_voices.close()
        self.pipeline.close(5.0)

    def _on_voice_audio(self, track, pcm, rate, offset):
        with self._lock:
            record = self._current
        if record is not None:
            self.live_voices.offer(record.session_id, track, pcm, rate, offset)

    def _begin_live_transcript(self, record, participants):
        self._live_record, self._live_participants = record, participants
        self._live_originals, self._live_display, self._live_voice_data = {}, {}, {}
        self.live_transcript_reset.emit(record.session_id if record else "")
        self.live_speaker_changed.emit("mic", "")
        self.live_speaker_changed.emit("system", "")

    def live_transcript_snapshot(self):
        return (self._live_record, sorted(self._live_display.values(),
                                         key=lambda segment: (segment.start, segment.track, segment.end)))

    @Slot(str, object)
    def _on_live_partial(self, session_id, segment):
        record = getattr(self, "_live_record", None)
        if record is None or record.session_id != session_id:
            return
        self._live_originals[segment_key(segment)] = segment
        self._publish_live_segment(segment)

    def _publish_live_segment(self, segment):
        from .voices import attribute_live_segment
        labelled = attribute_live_segment(segment, self._live_voice_data, self._live_participants)
        key = segment_key(segment)
        if self._live_display.get(key) != labelled:
            self._live_display[key] = labelled
            self.live_transcript_changed.emit(self._live_record.session_id, labelled)

    @Slot(str, str, object)
    def _on_voice_attributed(self, session_id, track, changed):
        record = getattr(self, "_live_record", None)
        if record is None or record.session_id != session_id:
            return
        entry = self.live_voices.sessions.get(session_id)
        if not entry:
            return
        data = entry["data"]
        self._live_voice_data = {"turns": data["turns"], "names": data["names"]}
        # Current-speaker feedback is independent of Whisper's chunk duration.
        if self._current is record:
            names = {data["names"].get(turn["speaker"], "") for turn in changed}
            name = next(iter(names)) if len(names) == 1 and "" not in names else ""
            if changed and track == "mic" and self._live_participants.microphone_is_me:
                name = self._live_participants.local_name or name
            if changed and track == "system" and self._live_participants.single_remote:
                name = self._live_participants.remote_name or name
            self.live_speaker_changed.emit(track, name)
        # Only reconsider passages intersecting this new evidence, not the whole meeting.
        for segment in self._live_originals.values():
            if segment.track == track and any(segment.start < turn["end"] and segment.end > turn["start"]
                                              for turn in changed):
                self._publish_live_segment(segment)

    def _on_live_voices_saved(self, record):
        if (self._current is not record and record.session_id not in self._processing
                and record.status == SessionStatus.COMPLETE):
            try:
                relabel_transcript(record, Participants.load(record.directory), self.repository)
            except (OSError, ValueError):
                self.message.emit("Nomi vocali non applicati; trascrizione conservata. Riprova dall'archivio.")
        self.voices_finished.emit(record.session_id)

    def _on_chunk_from_capture_thread(self, track: str, path: Path, offset: float) -> None:
        with self._lock:
            record = self._current
            if record is None:
                return
            speaker = (
                "Io"
                if track == "mic" and record.source is SessionSource.TEAMS
                else "Ambiente"
                if track == "mic"
                else "Partecipanti remoti"
            )
            session_id = record.session_id
        self.pipeline.submit_chunk(session_id, track, speaker, path, offset)

    @Slot(bool)
    def _on_teams_observed(self, active: bool) -> None:
        self._teams_active = active
        self.teams_changed.emit(active)
        if active:
            self.message.emit("Chiamata Teams rilevata.")
            if (
                self.config.auto_record_teams
                and not self._skip_current_teams_call
                and not self.is_recording
            ):
                self.start_recording(SessionSource.TEAMS)
        else:
            self._skip_current_teams_call = False
            with self._lock:
                record = self._current
            if record and record.source is SessionSource.TEAMS:
                self.stop_and_process()

    @Slot(str, str)
    def _on_pipeline_status(self, session_id: str, status: str) -> None:
        if session_id == "global":
            ready = status == "Whisper pronto"
            self.ai_changed.emit(not ready, status)
            self.message.emit(status)
            return
        if status != "Cancellata":
            self._processing.add(session_id)
            self.ai_changed.emit(True, status)
        else:
            self._processing.discard(session_id)
            self.ai_changed.emit(
                bool(self._processing), "Pronta" if not self._processing else status
            )
        self.message.emit(status)

    @Slot(object, object)
    def _on_pipeline_finished(self, record: SessionRecord, recap_path: Path | None) -> None:
        self._processing.discard(record.session_id)
        if hasattr(self, "live_voices") and (record.directory / "live-voices.json").exists():
            self._on_live_voices_saved(record)
        self.ai_changed.emit(
            bool(self._processing), "Pronta" if not self._processing else "In coda"
        )
        if recap_path:
            self.message.emit(f"Recap completato: {record.title}")
        else:
            self.message.emit(f"Trascrizione salvata. Recap solo su richiesta dall'archivio: {record.error or record.title}")
        self.session_finished.emit(record, recap_path)

    @Slot(str, str)
    def _on_pipeline_error(self, session_id: str, detail: str) -> None:
        self.error.emit(detail)
