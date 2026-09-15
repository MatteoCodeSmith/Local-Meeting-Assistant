"""Only constructed vectors/PCM and disposable DBs; no enrolled user profiles."""
from unittest.mock import Mock

import numpy as np

from local_meeting_assistant.domain import SessionSource
from local_meeting_assistant.storage import SessionRepository
from local_meeting_assistant.voice_database import VoiceDatabase
from local_meeting_assistant.live_voices import LiveVoices
from local_meeting_assistant.voices import load_analysis


def basis(index):
    result = np.zeros(192, dtype=np.float32)
    result[index] = 1
    return result


def test_database_persistence_match_margin_and_removal(tmp_path):
    database = VoiceDatabase(tmp_path / "profiles.sqlite3")
    assert database.match(basis(0)) is None
    identity = database.enroll("Persona Demo A", basis(0))
    database = VoiceDatabase(database.path)
    assert database.match(basis(0))["name"] == "Persona Demo A"
    assert database.match(basis(1)) is None
    database.enroll("Persona Demo B", basis(0) + 0.01 * basis(1))
    assert database.match(basis(0)) is None  # Two almost-identical candidates: abstain.
    database.delete(identity)
    assert all(item["name"] != "Persona Demo A" for item in database.list_profiles())


def test_reenrollment_keeps_identity_and_limits_reference_vectors(tmp_path):
    database = VoiceDatabase(tmp_path / "profiles.sqlite3")
    first = database.enroll("Nome Demo", basis(0))
    for _ in range(12):
        assert database.enroll("Nome Demo", basis(0)) == first
    assert len(database.list_profiles()[0]["vectors"]) == 8


def test_live_groups_known_and_unknown_voices_without_enrolling_guesses(tmp_path):
    repo = SessionRepository(tmp_path / "archive")
    record = repo.create(SessionSource.MANUAL)
    database = VoiceDatabase(tmp_path / "profiles.sqlite3")
    database.enroll("Persona Demo", basis(0))
    live = LiveVoices(repo, database)
    live.begin(record)
    entry = live.sessions[record.session_id]
    live._apply(entry, "mic", [{"start": 0, "end": 3, "embedding": basis(0).tolist()}])
    live._apply(entry, "system", [{"start": 0, "end": 3, "embedding": basis(1).tolist()}])
    live.end(record)
    data = load_analysis(record.directory)
    assert len(data["groups"]) == 2
    assert list(data["names"].values()) == ["Persona Demo"]
    assert len(database.list_profiles()) == 1
    live.close()


def test_live_queue_is_bounded_and_capture_does_not_invoke_ai(tmp_path):
    repo = SessionRepository(tmp_path / "archive")
    live = LiveVoices(repo, VoiceDatabase(tmp_path / "db.sqlite3"))
    for i in range(100):
        live.offer("demo", "mic", np.zeros((10, 1), dtype=np.float32), 16000, i * 3)
    assert live.pending.qsize() == 4
    assert live._dropped == 96
    assert not live._ready
    live.close()


def test_cancelled_session_cannot_be_recreated_by_late_voice_result(tmp_path):
    repo = SessionRepository(tmp_path / "archive")
    record = repo.create(SessionSource.MANUAL)
    live = LiveVoices(repo, VoiceDatabase(tmp_path / "db.sqlite3"))
    live.begin(record)
    live.end(record, cancelled=True)
    assert record.session_id not in live.sessions
    assert not (record.directory / "live-voices.json").exists()
    live.close()


def test_final_live_callback_unblocks_archive_controls(tmp_path):
    repo = SessionRepository(tmp_path / "archive")
    record = repo.create(SessionSource.MANUAL)
    live = LiveVoices(repo, VoiceDatabase(tmp_path / "db.sqlite3"))
    live.begin(record)
    live.sessions[record.session_id]["closed"] = True
    live._ready = True
    observations = []
    live.saved.connect(lambda item: observations.append(live.is_pending(item.session_id)))
    live._pump()
    assert observations == [False]
    live.close()


def test_voice_capture_callback_failure_does_not_escape():
    from local_meeting_assistant.recorder import _TrackCapture
    capture = _TrackCapture.__new__(_TrackCapture)
    capture.track, capture.sample_rate = "mic", 16000
    capture.callbacks = Mock()
    capture.callbacks.on_voice_audio.side_effect = RuntimeError("optional voice failure")
    capture._send_voice_audio([np.zeros((16000, 1), dtype=np.float32)], 0, np)
    capture.callbacks.on_voice_audio.assert_called_once()
