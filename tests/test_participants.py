from local_meeting_assistant.domain import TranscriptSegment, SessionSource, transcript_as_markdown
from local_meeting_assistant.participants import (
    Participants,
    read_segments,
    relabel_transcript,
    segment_key,
    backup_transcript,
)
from local_meeting_assistant.storage import SessionRepository


def test_explicit_names_and_per_turn_precedence(tmp_path):
    mic = TranscriptSegment(0, 5, "Questo è un test.", "Io", "mic")
    remote = TranscriptSegment(3, 7, "Seconda frase.", "Non identificato", "system")
    p = Participants("Matteo", True, "Luca", False)
    assert p.apply(mic).speaker == "Matteo"
    assert p.apply(remote).speaker == "Non identificato"
    p.single_remote = True
    assert p.apply(remote).speaker == "Luca"
    p.overrides[segment_key(remote)] = "Maria"
    p.save(tmp_path)
    assert Participants.load(tmp_path).apply(remote).speaker == "Maria"
    remote.text = "Testo ritrascritto differente"
    assert p.apply(remote).speaker == "Luca"


def test_archive_relabel_restore_and_backup(tmp_path):
    repo = SessionRepository(tmp_path)
    record = repo.create(SessionSource.TEAMS)
    mic = TranscriptSegment(0, 5, "Un test iniziale.", "Io", "mic")
    repo.save_transcript(record, [mic], transcript_as_markdown(record, [mic]))
    backup_transcript(record)
    relabel_transcript(record, Participants("Matteo", True), repo)
    assert read_segments(record.directory)[0].speaker == "Matteo"
    assert read_segments(record.directory)[0].speaker_source == "manual"
    relabel_transcript(record, Participants(), repo)
    assert read_segments(record.directory)[0].speaker == "Io"
    assert list((record.directory / "revisions").glob("*/transcript.md"))


def test_profile_is_not_shared_between_meetings(tmp_path):
    repo = SessionRepository(tmp_path)
    first, second = repo.create(SessionSource.TEAMS), repo.create(SessionSource.TEAMS)
    Participants("Matteo", True, "Luca", True).save(first.directory)
    assert Participants.load(second.directory).remote_name == ""


def test_pipeline_saves_manual_names_without_starting_recap(tmp_path):
    from unittest.mock import Mock
    from local_meeting_assistant.transcription import LocalProcessingPipeline

    repo = SessionRepository(tmp_path)
    record = repo.create(SessionSource.TEAMS)
    Participants("Matteo", True, "Luca", True).save(record.directory)
    pipeline = object.__new__(LocalProcessingPipeline)
    pipeline.repository = repo
    pipeline._segments = {record.session_id: [
        TranscriptSegment(0, 4, "Parlo dal microfono.", "Io", "mic"),
        TranscriptSegment(5, 8, "Rispondo da remoto.", "Partecipanti remoti", "system"),
    ]}
    pipeline._errors = {}
    pipeline.callbacks = Mock()
    pipeline._summarize_session = Mock()
    pipeline._finish_session(record)
    pipeline._summarize_session.assert_not_called()
    pipeline.callbacks.on_finished.assert_called_once_with(record, None)
    markdown = (record.directory / "transcript.md").read_text(encoding="utf-8")
    assert "— Matteo:" in markdown and "— Luca:" in markdown
    assert "— Luca:" in (record.directory / "transcript.md").read_text(encoding="utf-8")
