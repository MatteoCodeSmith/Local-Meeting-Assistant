"""Constructed vectors, invented names and disposable profiles only. No recordings or models."""
from collections import Counter
from unittest.mock import Mock, patch

import numpy as np
import pytest
from test_voices import fixture

from local_meeting_assistant.participants import read_segments
from local_meeting_assistant.voice_database import VoiceDatabase
from local_meeting_assistant.voice_engine import cluster_voices, drop_echo, merge_turns
from local_meeting_assistant.voice_ui import VoiceDialog
from local_meeting_assistant.voices import load_analysis, resolve_known_voices

RNG = np.random.default_rng(7)
COMMON = RNG.normal(size=192)


def unit(vector):
    return vector / np.linalg.norm(vector)


def centre():
    # Different synthetic "speakers" share a component: centres are ~0.25 alike, not orthogonal.
    return unit(0.6 * unit(COMMON) + unit(RNG.normal(size=192)))


def windows(point, count, noise=0.084):
    # Window-to-centre similarity ~0.6, close to noisy short ECAPA windows.
    return [unit(point + RNG.normal(scale=noise, size=192)) for _ in range(count)]


def synthetic_meeting(minutes, laughs=3):
    vectors, truth = [], []
    for speaker, length in enumerate(minutes):
        count = int(length * 30)
        vectors += windows(centre(), count)
        truth += [speaker] * count
    for laugh in range(laughs):
        vectors += windows(centre(), 3, 0.05)
        truth += [f"risata {laugh}"] * 3
    order = RNG.permutation(len(vectors))
    return [vectors[i] for i in order], [truth[i] for i in order]


def assignments(labels, truth):
    return {key: Counter(label for label, value in zip(labels, truth) if value == key).most_common(1)[0][0]
            for key in set(truth)}


@pytest.mark.parametrize("minutes", [[6, 4, 3, 2, 1], [6, 4, 3, 2, 1, .5]])
def test_declared_people_are_found_and_short_laughs_never_take_a_slot(minutes):
    vectors, truth = synthetic_meeting(minutes)
    labels, persons = cluster_voices(vectors, .55, len(minutes), [2.] * len(vectors), 8.)
    found = assignments(labels, truth)
    assert persons == len(minutes)
    assert sorted(found[speaker] for speaker in range(len(minutes))) == list(range(len(minutes)))
    assert all(found[f"risata {laugh}"] == -1 for laugh in range(3))


def test_voices_beyond_the_declared_count_stay_separate_groups():
    vectors, truth = synthetic_meeting([6, 4, 3, 2, 1, 1])
    labels, persons = cluster_voices(vectors, .55, 4, [2.] * len(vectors), 8., other_voices=True)
    found = assignments(labels, truth)
    assert persons == 4 and len({found[speaker] for speaker in range(6)}) == 6
    assert {found[4], found[5]} == {4, 5}  # "Altra voce" labels follow the people.
    forced, persons = cluster_voices(vectors, .55, 4, [2.] * len(vectors), 8., other_voices=False)
    assert persons == 4 and len({label for label in forced if label >= 0}) == 4


def test_overstated_count_does_not_promote_noise_to_people():
    vectors, truth = synthetic_meeting([5, 3, 2])
    labels, persons = cluster_voices(vectors, .55, 5, [2.] * len(vectors), 8.)
    assert persons == 3
    assert all(assignments(labels, truth)[f"risata {laugh}"] == -1 for laugh in range(3))


def test_microphone_echo_of_pc_audio_is_dropped_but_local_speech_kept():
    remote, local = unit(RNG.normal(size=192)), unit(RNG.normal(size=192))
    items = [({"track": "system", "start": 0., "end": 2.}, remote),
             ({"track": "mic", "start": .1, "end": 2.1}, unit(remote + .3 * local)),
             ({"track": "mic", "start": 5., "end": 7.}, local),
             ({"track": "mic", "start": 0., "end": 2.}, local)]
    keep = drop_echo([window for window, _ in items], [vector for _, vector in items])
    assert keep == [0, 2, 3]


def test_contiguous_windows_become_one_turn_without_moving_times():
    turns = merge_turns([{"track": "mic", "start": 0, "end": 2, "speaker": "Voce 1"},
                         {"track": "mic", "start": 2, "end": 4, "speaker": "Voce 1"},
                         {"track": "mic", "start": 4, "end": 6, "speaker": "Voce 2"},
                         {"track": "system", "start": 6, "end": 8, "speaker": "Voce 2"}])
    assert [(t["track"], t["start"], t["end"], t["speaker"]) for t in turns] == [
        ("mic", 0, 4, "Voce 1"), ("mic", 4, 6, "Voce 2"), ("system", 6, 8, "Voce 2")]


def test_realistic_similarity_matches_but_close_competitors_abstain(tmp_path):
    database = VoiceDatabase(tmp_path / "profiles.sqlite3")
    person = unit(RNG.normal(size=192))
    database.enroll("Persona Demo", person)
    sample = windows(person, 1)[0]
    assert .55 <= float(sample @ person) < .8
    assert database.match(sample) is None  # Live keeps its original, stricter defaults.
    assert database.match(sample, .55, .06)["name"] == "Persona Demo"
    assert database.match(unit(RNG.normal(size=192)), .55, .06) is None
    database.enroll("Gemello Demo", unit(person + .02 * RNG.normal(size=192)))
    assert database.match(sample, .55, .06) is None


def analysis_with_split_person(person, stranger):
    group = lambda key, vector, seconds, track: {
        "speaker": key, "kind": "persona", "seconds": seconds, "tracks": [track],
        "samples": [{"track": track, "start": 0, "end": 3}],
        "embedding": vector.tolist(), "reference_vectors": [vector.tolist()]}
    return {"expected": 2, "names": {},
            "groups": [group("Voce 1", stranger, 90, "system"),
                       group("Voce 2", unit(person + .05 * RNG.normal(size=192)), 60, "mic"),
                       group("Voce 3", unit(person + .05 * RNG.normal(size=192)), 30, "system"),
                       {"speaker": "Incerti", "kind": "incerto", "seconds": 4, "tracks": ["mic"],
                        "samples": []}],
            "turns": [{"track": "system", "start": 0, "end": 3, "speaker": "Voce 1"},
                      {"track": "mic", "start": 3, "end": 6, "speaker": "Voce 2"},
                      {"track": "system", "start": 6, "end": 9, "speaker": "Voce 3"},
                      {"track": "mic", "start": 9, "end": 10, "speaker": "Incerti"}]}


def test_known_voice_split_on_two_tracks_is_merged_named_and_never_enrolled(tmp_path):
    database = VoiceDatabase(tmp_path / "profiles.sqlite3")
    person, stranger = unit(RNG.normal(size=192)), unit(RNG.normal(size=192))
    identity = database.enroll("Persona Demo", person)
    data = resolve_known_voices(analysis_with_split_person(person, stranger), database)
    people = [group for group in data["groups"] if group["kind"] != "incerto"]
    assert [(g["speaker"], g["seconds"], g["kind"]) for g in people] == [
        ("Voce 1", 90, "persona"), ("Voce 2", 90, "persona")]
    assert people[1]["tracks"] == ["mic", "system"]
    assert data["names"] == {"Voce 2": "Persona Demo"}
    assert data["auto_names"]["Voce 2"]["profile_id"] == identity
    assert [turn["speaker"] for turn in data["turns"]] == ["Voce 1", "Voce 2", "Voce 2", "Incerti"]
    assert data["groups"][-1]["kind"] == "incerto"
    assert len(database.list_profiles()[0]["vectors"]) == 1


def test_finished_analysis_names_known_voices_as_automatic_until_confirmed(tmp_path):
    repo, record, _, ctrl = fixture(tmp_path)
    ctrl.voice_database = VoiceDatabase(tmp_path / "profiles.sqlite3")
    person = unit(RNG.normal(size=192))
    ctrl.voice_database.enroll("Persona Demo", person)
    data = {"expected": 1, "options": {"use_profiles": True}, "names": {},
            "groups": [{"speaker": "Voce 1", "kind": "persona", "seconds": 3, "tracks": ["mic"],
                        "samples": [{"track": "mic", "start": 0, "end": 3}],
                        "embedding": person.tolist()}],
            "turns": [{"track": "mic", "start": 0, "end": 3, "speaker": "Voce 1"}]}
    repo._write_json(record.directory / "voices.json", data)
    ctrl.ai_changed, ctrl.message, ctrl.voices_finished = Mock(), Mock(), Mock()
    ctrl._processing.add(record.session_id)
    ctrl._on_voices_finished(record, "")
    segment = read_segments(record.directory)[0]
    assert (segment.speaker, segment.speaker_source) == ("Persona Demo", "voice_profile")
    assert "riconosciute dalla rubrica" in ctrl.message.emit.call_args.args[0]
    ctrl.save_voice_names(record, {"Voce 1": "Persona Demo"})
    assert read_segments(record.directory)[0].speaker_source == "voice_confirmed"
    assert load_analysis(record.directory)["auto_names"] == {}


def test_dialog_search_finds_the_group_most_like_a_book_voice(tmp_path):
    repo, record, _, ctrl = fixture(tmp_path)
    ctrl.voice_database = VoiceDatabase(tmp_path / "profiles.sqlite3")
    person, stranger = unit(RNG.normal(size=192)), unit(RNG.normal(size=192))
    ctrl.voice_database.enroll("Persona Demo", person)
    data = analysis_with_split_person(person, stranger)
    repo._write_json(record.directory / "voices.json", data)
    dialog = VoiceDialog(ctrl, record)
    assert dialog.table.item(3, 0).text() == "Incerti"
    assert not dialog.table.item(3, 3).flags() & dialog.table.item(3, 3).flags().ItemIsEditable
    dialog.search_profile.setCurrentText("persona")
    dialog.find_profile()
    assert dialog.table.currentRow() in (1, 2)
    dialog.assign_profile()
    assert dialog.table.item(dialog.table.currentRow(), 3).text() == "Persona Demo"
    assert dialog.current_options()["min_group_seconds"] == 8.0
    ctrl.voices.start.assert_not_called()
    dialog.close()


def test_voice_book_opens_from_the_analysis_window(tmp_path):
    from PySide6.QtWidgets import QDialog
    _, record, _, ctrl = fixture(tmp_path)
    ctrl.voice_database = VoiceDatabase(tmp_path / "profiles.sqlite3")
    ctrl.voice_database.enroll("Persona Demo", unit(RNG.normal(size=192)))
    dialog = VoiceDialog(ctrl, record)
    opened = []
    with patch.object(QDialog, "exec", lambda self: opened.append(self) or 0):
        dialog.open_database()
    assert type(opened[0]).__name__ == "VoiceDatabaseDialog"
    assert opened[0].table.item(0, 0).text() == "Persona Demo"
    dialog.close()
