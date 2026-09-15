import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from local_meeting_assistant.domain import SessionSource, TranscriptSegment
from local_meeting_assistant.storage import SessionRepository


class StorageTests(TestCase):
    def test_create_and_save_transcript(self) -> None:
        with TemporaryDirectory() as folder:
            repository = SessionRepository(Path(folder))
            record = repository.create(SessionSource.MANUAL, "Test")
            segment = TranscriptSegment(0, 1, "ciao", "Ambiente", "mic", "it")
            json_path, markdown_path = repository.save_transcript(
                record, [segment], "# transcript\n"
            )
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload[0]["text"], "ciao")
            self.assertEqual(markdown_path.read_text(encoding="utf-8"), "# transcript\n")

