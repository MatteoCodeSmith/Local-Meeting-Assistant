"""Every recording, name and sentence here is synthetic. No microphone/model invocation."""
import threading
from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import QObject

from local_meeting_assistant.controller import AssistantController
from local_meeting_assistant.domain import SessionSource, SessionStatus, TranscriptSegment, transcript_as_markdown
from local_meeting_assistant.participants import Participants, read_segments, relabel_transcript, segment_key
from local_meeting_assistant.storage import SessionRepository
from local_meeting_assistant.voices import load_analysis, match_turn
from local_meeting_assistant.voice_jobs import VoiceJobs
from local_meeting_assistant.voice_ui import VoiceDialog


def fixture(tmp_path):
    repo = SessionRepository(tmp_path / "archive")
    record = repo.create(SessionSource.MANUAL, "Conversazione dimostrativa")
    record.status = SessionStatus.COMPLETE
    repo.save_record(record)
    data = {"groups": [{"speaker": "Voce 1", "tracks": ["mic"], "seconds": 3,
                         "samples": [{"track": "mic", "start": 0, "end": 3}]}],
            "turns": [{"track": "mic", "start": 0, "end": 3, "speaker": "Voce 1"}], "names": {}}
    repo._write_json(record.directory / "voices.json", data)
    segment = TranscriptSegment(0, 3, "Frase inventata per il test.", "Ambiente", "mic")
    repo.save_transcript(record, [segment], transcript_as_markdown(record, [segment]))
    ctrl = AssistantController.__new__(AssistantController)
    QObject.__init__(ctrl)
    ctrl._lock, ctrl._current, ctrl._processing = threading.RLock(), None, set()
    ctrl.repository = repo
    ctrl.voices = Mock(session_id=None)
    return repo, record, segment, ctrl


def test_voice_names_only_relabel_after_confirmation_and_can_be_cleared(tmp_path):
    repo, record, segment, ctrl = fixture(tmp_path)
    repo.save_recap(record, "Recap fittizio da conservare")
    assert read_segments(record.directory)[0].speaker == "Ambiente"
    assigned, uncertain = ctrl.save_voice_names(record, {"Voce 1": "Persona Demo"})
    assert (assigned, uncertain) == (1, 0)
    labelled = read_segments(record.directory)[0]
    assert (labelled.text, labelled.start, labelled.end) == (segment.text, segment.start, segment.end)
    assert labelled.speaker == "Persona Demo" and labelled.speaker_source == "voice_confirmed"
    assert (record.directory / "recap.md").read_text(encoding="utf-8").strip() == "Recap fittizio da conservare"
    assert list((record.directory / "revisions").glob("*/transcript.json"))
    ctrl.save_voice_names(record, {"Voce 1": ""})
    assert read_segments(record.directory)[0].speaker == "Ambiente"


def test_mixed_short_or_wrong_track_passages_are_not_assigned():
    segment = TranscriptSegment(0, 3, "Test fittizio", "Remoti", "system")
    turns = [{"track": "system", "start": 0, "end": 2.8, "speaker": "A"},
             {"track": "system", "start": 2.8, "end": 3, "speaker": "B"}]
    assert match_turn(segment, turns) is None
    assert match_turn(segment, [{"track": "mic", "start": 0, "end": 3, "speaker": "A"}]) is None
    assert match_turn(segment, [{"track": "system", "start": 0, "end": 1, "speaker": "A"}]) is None
    assert match_turn(segment, [{"track": "system", "start": 0, "end": 3, "speaker": "A"}]) == "A"


def test_manual_overrides_win_and_voice_labels_survive_relabel(tmp_path):
    repo, record, segment, ctrl = fixture(tmp_path)
    ctrl.save_voice_names(record, {"Voce 1": "Demo voce"})
    relabel_transcript(record, Participants(), repo)
    assert read_segments(record.directory)[0].speaker == "Demo voce"
    participants = Participants(overrides={segment_key(segment): "Correzione manuale"})
    participants.save(record.directory)
    ctrl.save_voice_names(record, {"Voce 1": "Altro nome"})
    assert read_segments(record.directory)[0].speaker == "Correzione manuale"


def test_voice_analysis_allowed_without_transcript(tmp_path):
    repo, record, _, ctrl = fixture(tmp_path)
    # Synthetic-only removal to model an old audio session without a transcript.
    (record.directory / "transcript.json").unlink()
    (record.directory / "transcript.md").unlink()
    assert ctrl.save_voice_names(record, {"Voce 1": "Nome Demo"}) == (0, 0)
    assert load_analysis(record.directory)["names"]["Voce 1"] == "Nome Demo"


def test_controller_refuses_analysis_or_label_changes_when_busy(tmp_path):
    _, record, _, ctrl = fixture(tmp_path)
    ctrl._current = record
    assert not ctrl.analyse_voices(record)
    ctrl.voices.start.assert_not_called()
    with pytest.raises(ValueError):
        ctrl.save_voice_names(record, {"Voce 1": "Demo"})
    ctrl._current = None
    ctrl._processing.add("another-session")
    assert not ctrl.analyse_voices(record)


def test_cancelled_process_does_not_publish_result(tmp_path):
    repo, record, _, _ = fixture(tmp_path)
    jobs = VoiceJobs(repo)
    process = Mock()
    process.readAllStandardOutput.return_value = b""
    jobs._process, jobs._cancelled, jobs._buffer = process, False, b""
    jobs._error_type = ""
    result = record.directory / "fake-result.json"
    repo._write_json(result, {"names": {"Voce 1": "Non confermato"}})
    jobs.cancel()
    process.kill.assert_called_once()
    jobs._finish(process, record, result, 0)
    assert load_analysis(record.directory)["names"] == {}


def test_completed_analysis_does_not_apply_names_to_transcript(tmp_path):
    repo, record, segment, _ = fixture(tmp_path)
    jobs = VoiceJobs(repo)
    process = Mock()
    process.readAllStandardOutput.return_value = b""
    jobs._process, jobs._cancelled, jobs._buffer = process, False, b""
    jobs._error_type = ""
    run = record.directory / "voice-runs" / "synthetic"
    run.mkdir(parents=True)
    result = run / "analysis.json"
    data = load_analysis(record.directory)
    data["names"] = {"Voce 1": "Test"}
    repo._write_json(result, data)
    jobs._finish(process, record, result, 0)
    assert read_segments(record.directory)[0] == segment
    assert (run / "previous-review.json").exists()


def test_voice_dialog_never_starts_analysis_or_playback_on_open(tmp_path):
    _, record, _, ctrl = fixture(tmp_path)
    with patch("sounddevice.play") as play:
        dialog = VoiceDialog(ctrl, record)
        dialog.show()
        assert dialog.table.rowCount() == 1
        ctrl.voices.start.assert_not_called()
        play.assert_not_called()
        assert dialog.table.item(0, 3).text() == ""
        dialog.table.item(0, 3).setText("Demo confermata")
        dialog.apply_names()
        assert read_segments(record.directory)[0].speaker == "Demo confermata"
        dialog.close()


def test_invalid_name_does_not_change_saved_analysis(tmp_path):
    repo, record, _, ctrl = fixture(tmp_path)
    original = (record.directory / "voices.json").read_bytes()
    with pytest.raises(ValueError):
        ctrl.save_voice_names(record, {"Voce 1": "<bad>"})
    assert (record.directory / "voices.json").read_bytes() == original
