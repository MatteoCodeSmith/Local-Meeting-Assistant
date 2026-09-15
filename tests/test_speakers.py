from local_meeting_assistant.domain import TranscriptSegment
from local_meeting_assistant.speakers import (
    UNKNOWN_REMOTE,
    SpeakerTimeline,
    TeamsSpeakerMonitor,
    speaker_from_label,
)


def segment(start=0, end=5, track="system"):
    return TranscriptSegment(
        start, end, "Una frase di esempio.", "Io" if track == "mic" else "Remoti", track
    )


def test_only_explicit_active_speaker_labels():
    assert speaker_from_label("Maria Rossi, speaking") == "Maria Rossi"
    assert speaker_from_label("Parla: Luca Bianchi") == "Luca Bianchi"
    assert speaker_from_label("Maria Rossi sta parlando") == "Maria Rossi"
    for text in [
        "Maria Rossi",
        "Maria Rossi, unmuted",
        "Maria Rossi, in conversazione",
        "Maria Rossi, not speaking",
        "You, speaking",
        "Show speaking participants",
    ]:
        assert speaker_from_label(text) is None


def test_timestamped_names_roundtrip_and_retranscription(tmp_path):
    t = SpeakerTimeline()
    t.add(0, 2, ("Maria Rossi",))
    t.add(2, 5, ("Maria Rossi",))
    t.save(tmp_path)
    restored = SpeakerTimeline.load(tmp_path)
    assert len(restored.spans) == 1
    named = restored.attribute(segment())
    assert named.speaker == "Maria Rossi" and named.speaker_source == "teams_uia_approximate"
    assert restored.attribute(segment(track="mic")).speaker == "Io"


def test_no_majority_guess_for_switches_or_overlaps():
    for names in [("Luca",), ("Maria", "Luca")]:
        t = SpeakerTimeline()
        t.add(0, 4.8, ("Maria",))
        t.add(4.8, 5, names)
        assert t.attribute(segment()).speaker == UNKNOWN_REMOTE


def test_gaps_and_old_recordings_remain_unknown(tmp_path):
    assert SpeakerTimeline.load(tmp_path).attribute(segment()).speaker == UNKNOWN_REMOTE
    t = SpeakerTimeline()
    t.add(0, 2, ("Maria",))
    t.add(4, 5, ("Maria",))
    assert t.attribute(segment()).speaker == UNKNOWN_REMOTE


def test_stale_and_future_observations_not_reused():
    monitor = TeamsSpeakerMonitor()
    monitor.set_recording(True)
    monitor._observation = (10, ("Maria",))
    assert monitor.names_at(10.5) == ("Maria",)
    assert monitor.names_at(12) == ()
    assert monitor.names_at(9) == ()
    monitor.set_recording(False)
    assert monitor.names_at(10.5) == ()
