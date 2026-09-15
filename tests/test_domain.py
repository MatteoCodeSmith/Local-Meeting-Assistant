from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from local_meeting_assistant.domain import (
    SessionRecord,
    SessionSource,
    TranscriptSegment,
    merge_segments,
    transcript_as_markdown,
)


class DomainTests(TestCase):
    def test_merge_segments_is_chronological(self) -> None:
        later = TranscriptSegment(12, 14, "secondo", "Io", "mic", "it")
        earlier = TranscriptSegment(2, 4, "first", "Remote", "system", "en")
        self.assertEqual(merge_segments([later, earlier]), [earlier, later])

    def test_markdown_contains_timestamps_and_speakers(self) -> None:
        record = SessionRecord(
            session_id="abc",
            directory=Path("unused"),
            source=SessionSource.MANUAL,
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            title="Idea",
        )
        result = transcript_as_markdown(
            record, [TranscriptSegment(65, 67, "  una   idea ", "Ambiente", "mic")]
        )
        self.assertIn("# Trascrizione — Idea", result)
        self.assertIn("**01:05 — Ambiente:** una idea", result)

