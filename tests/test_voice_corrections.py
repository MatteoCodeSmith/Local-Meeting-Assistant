"""Synthetic PCM, invented text and disposable profiles only. No real recordings."""
from unittest.mock import Mock, patch
from dataclasses import replace

import numpy as np
import pytest
import soundfile as sf

from local_meeting_assistant.participants import Participants, read_segments, segment_key
from local_meeting_assistant.voice_corrections import VoiceErrorDatabase, allowed_spans, filtered_analysis
from local_meeting_assistant.voice_corrections_ui import VoiceCorrectionsDialog
from local_meeting_assistant.voice_database import VoiceDatabase
from local_meeting_assistant.voice_engine import analyse, cluster_vectors
from local_meeting_assistant.voice_jobs import VoiceJobs
from local_meeting_assistant.voice_ui import VoiceDialog
from local_meeting_assistant.voices import apply_saved_voice_names, load_analysis
from test_voices import fixture


def basis(index):
    result = np.zeros(192, dtype=np.float32)
    result[index] = 1
    return result


def synthetic_audio(directory, seconds=3):
    sf.write(directory / "mic.flac", np.zeros(int(seconds * 16000)), 16000)


def test_archive_start_uses_supported_signature_and_clears_failed_state(tmp_path):
    _, record, _, ctrl = fixture(tmp_path)
    synthetic_audio(record.directory)
    ctrl.voices.start = Mock(spec=VoiceJobs.start)
    assert ctrl.analyse_voices(record, 5, 0.8)
    ctrl.voices.start.assert_called_once_with(record, 5, 0.8, None)
    ctrl._processing.clear()
    ctrl.voices.start.side_effect = TypeError("synthetic startup failure")
    assert not ctrl.analyse_voices(record, 2)
    assert record.session_id not in ctrl._processing


def test_blacklist_persists_isolated_by_session_and_is_reversible(tmp_path):
    a, b = tmp_path / "archive" / "session-a", tmp_path / "archive" / "session-b"
    db = VoiceErrorDatabase.for_session(a)
    span = {"track": "mic", "start": 1., "end": 3.}
    db.add(a, [span], "Risata")
    saved = VoiceErrorDatabase(db.path).list(a)
    assert len(saved) == 1 and saved[0]["reason"] == "Risata"
    assert db.list(b) == []
    db.restore(b, saved[0]["id"])
    assert db.list(a)
    db.restore(a, saved[0]["id"])
    assert db.list(a) == []


@pytest.mark.parametrize("span", [
    {"track": "mic", "start": -1, "end": 2},
    {"track": "mic", "start": 2, "end": 1},
    {"track": "mic", "start": 0, "end": float("nan")},
    {"track": "mic", "start": 0, "end": float("inf")},
    {"track": "../secret", "start": 0, "end": 1},
])
def test_invalid_corrections_never_create_database(tmp_path, span):
    db = VoiceErrorDatabase(tmp_path / "errors.sqlite3")
    with pytest.raises(ValueError):
        db.add(tmp_path / "session", [span], "Risata")
    assert not db.path.exists()


def test_subtraction_preserves_track_and_absolute_times():
    exclusions = [{"track": "mic", "start": 3, "end": 6},
                  {"track": "mic", "start": 2, "end": 4}]
    assert allowed_spans("mic", 0, 10, exclusions) == [(0, 2), (6, 10)]
    assert allowed_spans("system", 0, 10, exclusions) == [(0, 10)]


def test_partial_exclusion_invalidates_contaminated_embedding():
    turns = [{"speaker": "A", "track": "mic", "start": 0, "end": 3},
             {"speaker": "A", "track": "mic", "start": 6, "end": 9}]
    data = {"turns": turns, "groups": [{"speaker": "A", "embedding": [1],
            "reference_vectors": [[1]], "samples": turns, "seconds": 6}]}
    filtered = filtered_analysis(data, [{"track": "mic", "start": 1, "end": 2}])
    assert filtered["turns"] == [turns[1]]
    group = filtered["groups"][0]
    assert group["needs_reanalysis"] and "embedding" not in group and "reference_vectors" not in group
    assert group["seconds"] == 3
    assert "embedding" in data["groups"][0]  # Raw evidence remains recoverable.


def test_correction_keeps_all_text_audio_recap_and_profiles_and_undo_recovers_name(tmp_path):
    repo, record, original, ctrl = fixture(tmp_path)
    synthetic_audio(record.directory)
    audio_before = (record.directory / "mic.flac").read_bytes()
    repo.save_recap(record, "Recap dimostrativo invariato")
    recap_before = (record.directory / "recap.md").read_bytes()
    database = VoiceDatabase(tmp_path / "voice-profiles.sqlite3")
    database.enroll("Demo", basis(0))
    profiles_before = database.path.read_bytes()
    ctrl.save_voice_names(record, {"Voce 1": "Demo"})
    ctrl.correct_voice_intervals(record, [{"track": "mic", "start": 0, "end": 3}], "Risata")
    assert read_segments(record.directory) == [original]
    assert load_analysis(record.directory)["groups"] == []
    assert load_analysis(record.directory)["names"] == {"Voce 1": "Demo"}
    assert apply_saved_voice_names(record.directory, [original]) == [original]
    assert database.path.read_bytes() == profiles_before
    assert (record.directory / "mic.flac").read_bytes() == audio_before
    assert (record.directory / "recap.md").read_bytes() == recap_before
    exclusion = VoiceErrorDatabase.for_session(record.directory).list(record.directory)[0]
    ctrl.correct_voice_intervals(record, restore_id=exclusion["id"])
    restored = read_segments(record.directory)
    assert len(restored) == 1 and restored[0].text == original.text
    assert restored[0].speaker == "Demo"


def test_manual_names_win_and_busy_corrections_are_refused(tmp_path):
    _, record, original, ctrl = fixture(tmp_path)
    synthetic_audio(record.directory)
    Participants(overrides={segment_key(original): "Manuale Demo"}).save(record.directory)
    ctrl.correct_voice_intervals(record, [{"track": "mic", "start": 0, "end": 3}], "Rumore")
    assert read_segments(record.directory)[0].speaker == "Manuale Demo"
    ctrl._current = record
    with pytest.raises(ValueError):
        ctrl.correct_voice_intervals(record, [{"track": "mic", "start": 0, "end": 3}])


def test_exclusion_beyond_audio_is_rejected(tmp_path):
    _, record, _, ctrl = fixture(tmp_path)
    synthetic_audio(record.directory)
    with pytest.raises(ValueError):
        ctrl.correct_voice_intervals(record, [{"track": "mic", "start": 0, "end": 50}])
    assert VoiceErrorDatabase.for_session(record.directory).list(record.directory) == []


def test_encoder_never_sees_excluded_audio_and_reruns_keep_timestamps(tmp_path):
    # Positive tone-like constant and negative artificial noise; not human speech.
    pcm = np.full(9 * 16000, .2, dtype=np.float32)
    pcm[3 * 16000:6 * 16000] = -.5
    sf.write(tmp_path / "mic.flac", pcm, 16000)
    exclusions = [{"track": "mic", "start": 3, "end": 6}]
    encoder = Mock()
    encoder.encode.side_effect = lambda audio: basis(0) if audio.min() > 0 else pytest.fail("Rejected audio reached encoder")
    detector = lambda audio: [{"start": 0, "end": len(audio)}]
    for expected in (1, 5):
        result = analyse(tmp_path, tmp_path / "unused", expected=expected, encoder=encoder,
                         speech_detector=detector, exclusions=exclusions)
        assert [(turn["start"], turn["end"]) for turn in result["turns"]] == [(0, 3), (6, 9)]
        assert result["expected"] == expected
    encoder.reset_mock()
    result = analyse(tmp_path, tmp_path / "unused", expected=8, encoder=encoder,
                     speech_detector=detector, exclusions=[{"track": "mic", "start": 0, "end": 9}])
    assert result["groups"] == [] and result["turns"] == []
    encoder.encode.assert_not_called()


@pytest.mark.parametrize("count", [1, 2, 5, 8])
def test_actual_speaker_count_with_separated_synthetic_vectors(count):
    vectors = [basis(index) for index in range(count) for _ in range(3)]
    assert len(set(cluster_vectors(vectors, expected=count))) == count


def test_archive_proposals_require_consensus_and_preserve_legacy_profiles(tmp_path):
    db = VoiceDatabase(tmp_path / "profiles.sqlite3")
    identity = db.enroll("Demo A", basis(0))
    db.enroll("Demo B", basis(1))
    group = {"embedding": basis(0).tolist()}
    assert db.match_group(group)["id"] == identity
    group["reference_vectors"] = [basis(0), basis(0), basis(1)]
    assert db.match_group(group) is None
    group["reference_vectors"] = [basis(0), basis(0), basis(2)]
    assert db.match_group(group)["id"] == identity
    group["needs_reanalysis"] = True
    assert db.match_group(group) is None
    assert len(db.list_profiles()) == 2


def test_dialog_corrections_do_not_start_ai_or_play_audio(tmp_path):
    _, record, _, ctrl = fixture(tmp_path)
    synthetic_audio(record.directory)
    with patch("sounddevice.play") as play:
        dialog = VoiceCorrectionsDialog(ctrl, record, "Voce 1")
        dialog.show()
        dialog.exclude_interval()
        assert dialog.table.rowCount() == 1
        assert read_segments(record.directory)[0].text == "Frase inventata per il test."
        dialog.restore()
        assert dialog.table.rowCount() == 0
        play.assert_not_called()
        ctrl.voices.start.assert_not_called()
        ctrl._current = record
        dialog.update_actions()
        assert not dialog.exclude_button.isEnabled()
        dialog.close()


def test_voice_dialog_restores_previous_count_without_running_analysis(tmp_path):
    repo, record, _, ctrl = fixture(tmp_path)
    data = load_analysis(record.directory)
    data.update(expected=8, threshold=0.65, options={"window": 3.0, "other_voices": False})
    repo._write_json(record.directory / "voices.json", data)
    dialog = VoiceDialog(ctrl, record)
    assert dialog.expected.value() == 8
    assert dialog.threshold.currentData() == .65
    assert dialog.window_length.currentData() == 3.0 and not dialog.other_voices.isChecked()
    assert dialog.corrections_button.isEnabled()
    ctrl.voices.start.assert_not_called()
    dialog.close()


def test_unreadable_blacklist_does_not_break_whisper_text_or_bypass_corrections(tmp_path):
    _, record, original, ctrl = fixture(tmp_path)
    database = VoiceErrorDatabase.for_session(record.directory)
    database.path.write_bytes(b"synthetic damaged database")
    automatic = replace(original, speaker="Demo", speaker_source="voice_profile")
    restored = apply_saved_voice_names(record.directory, [automatic])
    assert restored == [original]
    assert load_analysis(record.directory)["corrections_unavailable"]
    with pytest.raises(ValueError, match="Blacklist"):
        ctrl.save_voice_names(record, {"Voce 1": "Demo"})


def test_archive_worker_loads_corrections_before_inference(tmp_path, monkeypatch):
    from contextlib import nullcontext
    import os
    import sys
    from local_meeting_assistant import voice_worker
    directory = tmp_path / "archive" / "synthetic"
    directory.mkdir(parents=True)
    database = VoiceErrorDatabase.for_session(directory)
    database.add(directory, [{"track": "mic", "start": 1, "end": 2}], "Risata")
    output = tmp_path / "synthetic-result.json"
    monkeypatch.setattr(sys, "argv", ["worker", "--directory", str(directory),
        "--checkpoint", str(tmp_path / "unused"), "--output", str(output), "--expected", "8"])
    # The worker's network/environment restrictions must not leak to other tests.
    import socket
    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, getattr(socket.socket, name))
    monkeypatch.setattr(socket, "create_connection", socket.create_connection)
    priority = patch("ctypes.windll.kernel32.SetPriorityClass") if os.name == "nt" else nullcontext()
    with patch.dict("os.environ"), priority, \
            patch("local_meeting_assistant.voice_engine.analyse", return_value={"turns": [], "groups": []}) as engine:
        assert voice_worker.main() == 0
    assert engine.call_args.kwargs["expected"] == 8
    assert engine.call_args.kwargs["exclusions"][0]["reason"] == "Risata"
