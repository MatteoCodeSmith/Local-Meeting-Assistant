import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from local_meeting_assistant.config import AppConfig
from local_meeting_assistant.domain import SessionSource, SessionStatus
from local_meeting_assistant.storage import SessionRepository
from local_meeting_assistant.ui import ArchiveDialog, OverlayWindow, RecognitionDialog, _icon_pixmap
from local_meeting_assistant.ui import ParticipantsDialog
from local_meeting_assistant.ui import RecapDialog


class FakeController(QObject):
    recording_changed = Signal(bool, str)
    track_changed = Signal(str, bool)
    level_changed = Signal(str, float)
    ai_changed = Signal(bool, str)
    teams_changed = Signal(bool)
    message = Signal(str)
    error = Signal(str)
    session_finished = Signal(object, object)

    def __init__(self) -> None:
        super().__init__()
        self.sessions = []
        self.is_recording = False

    def start_manual(self) -> None:
        pass

    def stop_and_process(self) -> None:
        pass

    def cancel_and_delete(self) -> None:
        pass

    def list_sessions(self):
        return self.sessions

    def retranscribe_session(self, _record) -> bool:
        return True

    def generate_recap(self, _record) -> bool:
        return True


def test_recognition_dialog_saves_options_without_starting_recording(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    app = QApplication.instance() or QApplication([])
    controller = FakeController()
    controller.speaker_monitor = SimpleNamespace(status="Indicatore non disponibile", set_recording=Mock())
    config = AppConfig()
    monkeypatch.setattr(AppConfig, "save", lambda self: tmp_path / "config.json")
    dialog = RecognitionDialog(controller, config)
    assert "non disponibile" in dialog.probe_status.text()
    dialog.language.setCurrentIndex(dialog.language.findData("en"))
    dialog.names.setChecked(False)
    dialog.local_name.setText("Matteo")
    dialog.save()
    assert config.transcription_language == "en" and not config.teams_speaker_names
    assert config.local_display_name == "Matteo"
    controller.speaker_monitor.set_recording.assert_called_once_with(False)
    dialog.close()
    app.processEvents()


def test_overlay_collapses_and_expands(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    overlay = OverlayWindow(FakeController(), AppConfig(recordings_dir=str(tmp_path)))

    assert (overlay.width(), overlay.height()) == (88, 88)
    assert overlay.panel.isHidden()

    overlay._set_expanded(True)
    assert (overlay.width(), overlay.height()) == (770, 116)
    assert not overlay.panel.isHidden()

    overlay._set_expanded(False)
    assert _icon_pixmap(64).hasAlphaChannel()
    overlay.close()
    app.processEvents()


def test_names_dialog_saves_typed_per_turn_name(tmp_path):
    from unittest.mock import Mock
    from local_meeting_assistant.domain import TranscriptSegment, transcript_as_markdown
    from local_meeting_assistant.participants import segment_key

    app = QApplication.instance() or QApplication([])
    repo = SessionRepository(tmp_path)
    record = repo.create(SessionSource.TEAMS)
    segment = TranscriptSegment(0, 4, "Una frase per il test.", "Remoto", "system")
    repo.save_transcript(record, [segment], transcript_as_markdown(record, [segment]))
    controller = FakeController()
    controller.save_participants = Mock(return_value=True)
    dialog = ParticipantsDialog(controller, record)
    dialog.table.item(0, 3).setText("Luca")
    dialog.save()
    saved = controller.save_participants.call_args.args[1]
    assert saved.overrides[segment_key(segment)] == "Luca" and not saved.single_remote
    dialog.close()
    app.processEvents()


def test_archive_lists_saved_sessions(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    repository = SessionRepository(tmp_path)
    record = repository.create(SessionSource.MANUAL, "Prova archivio")
    record.status = SessionStatus.COMPLETE
    repository.save_record(record)
    (record.directory / "system.flac").write_bytes(b"audio")
    controller = FakeController()
    controller.sessions = [record]

    dialog = ArchiveDialog(controller)

    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 1).text() == "Prova archivio"
    assert dialog.table.item(0, 3).text() == "PC"
    assert dialog.transcribe_button.isEnabled()
    assert not dialog.recap_button.isEnabled()
    dialog.close()
    app.processEvents()


def test_recap_dialog_saves_model_context_and_italian_only(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    config = AppConfig()
    monkeypatch.setattr(AppConfig, "save", lambda self: tmp_path / "config.json")
    dialog = RecapDialog(config)
    dialog.model.setCurrentText("qwen3.5-9b")
    dialog.context.setValue(32768)
    dialog.gpu_cache.setChecked(True)
    dialog.save()
    assert config.lm_model == "qwen3.5-9b"
    assert config.lm_context_length == 32768 and config.recap_language == "Italiano"
    assert config.summary_chunk_characters == 48000
    assert config.lm_gpu_kv_cache
    dialog.close()
    app.processEvents()


def test_recap_dialog_excludes_voice_models():
    from unittest.mock import patch
    from local_meeting_assistant.summarizer import LMStudioSummarizer
    app = QApplication.instance() or QApplication([])
    dialog = RecapDialog(AppConfig())
    with patch.object(LMStudioSummarizer, "_native_request", return_value={"models": [
        {"key": "speechbrain-spkrec-ecapa-voxceleb", "type": "llm"},
        {"key": "qwen3.5-9b", "type": "llm"},
    ]}):
        dialog.refresh_models()
    assert dialog.model.count() == 1 and dialog.model.itemText(0) == "qwen3.5-9b"
    dialog.close()
    app.processEvents()


def test_recap_history_lists_fake_metadata_and_opens_only_on_click(tmp_path):
    from unittest.mock import patch
    from local_meeting_assistant.ui import RecapHistoryDialog
    repo = SessionRepository(tmp_path)
    record = repo.create(SessionSource.MANUAL, "Test fittizio")
    repo.save_recap(record, "Testo fittizio", model_info={"model": "test-model", "context_length": 16384})
    with patch("local_meeting_assistant.ui.QDesktopServices.openUrl") as open_url:
        dialog = RecapHistoryDialog(repo, record)
        assert dialog.table.rowCount() == 1
        assert dialog.table.item(0, 1).text() == "test-model"
        open_url.assert_not_called()
        dialog.open_selected()
        open_url.assert_called_once()
    dialog.close()
