"""Synthetic in-memory timeline: no models, profiles, audio or real transcripts loaded."""
from types import SimpleNamespace

from PySide6.QtCore import QObject

from local_meeting_assistant.config import AppConfig
from local_meeting_assistant.controller import AssistantController
from local_meeting_assistant.domain import SessionRecord, SessionSource, TranscriptSegment
from local_meeting_assistant.participants import Participants, segment_key
from local_meeting_assistant.live_transcript_ui import LiveTranscriptDialog
from local_meeting_assistant.ui import OverlayWindow


def setup(tmp_path, participants=None):
    controller = AssistantController.__new__(AssistantController)
    QObject.__init__(controller)
    record = SessionRecord("synthetic", tmp_path, SessionSource.MANUAL)
    controller._current = record
    controller._begin_live_transcript(record, participants or Participants())
    data = {"turns": [], "names": {"known": "Persona Demo"}}
    controller.live_voices = SimpleNamespace(sessions={record.session_id: {"data": data}})
    return controller, record, data


def evidence(controller, record, data, start=0, end=3, speaker="known", track="mic"):
    span = {"start": start, "end": end, "speaker": speaker, "track": track}
    data["turns"].append(span)
    controller._on_voice_attributed(record.session_id, track, [span])


def passage():
    return TranscriptSegment(0, 3, "Frase inventata per la verifica.", "Ambiente", "mic")


def test_name_known_before_whisper_is_in_first_partial(tmp_path):
    ctrl, record, data = setup(tmp_path)
    evidence(ctrl, record, data)
    observed = []
    ctrl.live_transcript_changed.connect(lambda _id, segment: observed.append(segment))
    source = passage()
    ctrl._on_live_partial(record.session_id, source)
    assert len(observed) == 1 and observed[0].speaker == "Persona Demo"
    assert observed[0].speaker_source == "voice_profile"
    assert source.speaker == "Ambiente"  # Original pipeline data is never mutated.


def test_late_voice_updates_existing_row_without_retranscription(tmp_path):
    ctrl, record, data = setup(tmp_path)
    ctrl._on_live_partial(record.session_id, passage())
    dialog = LiveTranscriptDialog(ctrl)
    assert dialog.table.item(0, 1).text() == "Ambiente"
    evidence(ctrl, record, data)
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 1).text() == "Persona Demo"
    assert dialog.table.item(0, 2).text() == passage().text
    assert not list(tmp_path.iterdir())  # No dependency on periodic disk snapshots.
    dialog.close()


def test_late_conflicting_voice_revokes_name_and_manual_override_wins(tmp_path):
    ctrl, record, data = setup(tmp_path)
    evidence(ctrl, record, data, end=2.5)
    ctrl._on_live_partial(record.session_id, passage())
    assert ctrl.live_transcript_snapshot()[1][0].speaker == "Persona Demo"
    evidence(ctrl, record, data, start=2.5, end=3, speaker="unknown")
    assert ctrl.live_transcript_snapshot()[1][0].speaker == "Ambiente"
    ctrl._live_participants = Participants(overrides={segment_key(passage()): "Correzione manuale"})
    ctrl._publish_live_segment(passage())
    assert ctrl.live_transcript_snapshot()[1][0].speaker == "Correzione manuale"


def test_no_name_bleeds_into_other_track_future_passage_or_new_meeting(tmp_path):
    ctrl, record, data = setup(tmp_path)
    evidence(ctrl, record, data)
    remote = TranscriptSegment(0, 3, "Altro testo fittizio", "Remoti", "system")
    later = TranscriptSegment(20, 23, "Testo futuro", "Ambiente", "mic")
    ctrl._on_live_partial(record.session_id, remote)
    ctrl._on_live_partial(record.session_id, later)
    assert [segment.speaker for segment in ctrl.live_transcript_snapshot()[1]] == ["Remoti", "Ambiente"]
    replacement = SessionRecord("second-demo", tmp_path, SessionSource.MANUAL)
    ctrl._begin_live_transcript(replacement, Participants())
    ctrl._on_live_partial(record.session_id, passage())
    evidence(ctrl, record, data)
    assert ctrl.live_transcript_snapshot()[1] == []


def test_empty_voice_window_clears_current_name_but_not_past_attribution(tmp_path):
    ctrl, record, data = setup(tmp_path)
    feedback = []
    ctrl.live_speaker_changed.connect(lambda track, name: feedback.append((track, name)))
    evidence(ctrl, record, data)
    ctrl._on_live_partial(record.session_id, passage())
    ctrl._on_voice_attributed(record.session_id, "mic", [])
    assert feedback[-1] == ("mic", "")
    assert ctrl.live_transcript_snapshot()[1][0].speaker == "Persona Demo"


def test_widget_shows_name_immediately_and_live_table_sorts_tracks(tmp_path):
    ctrl, record, data = setup(tmp_path)
    widget = OverlayWindow(ctrl, AppConfig(), preview=True)
    ctrl.recording_changed.emit(True, "manual")
    evidence(ctrl, record, data)
    assert "Persona Demo" in widget.status_label.text()
    ctrl.message.emit("Trascrizione in corso")
    assert "Persona Demo" in widget.status_label.text()
    ctrl._on_voice_attributed(record.session_id, "mic", [])
    assert widget.status_label.text() == "Trascrizione in corso"
    dialog = LiveTranscriptDialog(ctrl)
    ctrl._on_live_partial(record.session_id, TranscriptSegment(10, 12, "Dopo", "Remoti", "system"))
    ctrl._on_live_partial(record.session_id, passage())
    assert dialog.table.rowCount() == 2
    assert dialog.table.item(0, 2).text() == passage().text
    assert dialog.table.item(1, 2).text() == "Dopo"
    ctrl._begin_live_transcript(None, Participants())
    assert dialog.table.rowCount() == 0
    dialog.close()
    widget.close()
