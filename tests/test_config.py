import json
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import TestCase

from local_meeting_assistant.config import AppConfig


class ConfigTests(TestCase):
    def test_round_trip_and_unknown_fields(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            config = AppConfig(whisper_model="tiny", auto_record_teams=False)
            config.save(path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["future_field"] = 123
            path.write_text(json.dumps(raw), encoding="utf-8")
            loaded = AppConfig.load(path)
            self.assertEqual(loaded.whisper_model, "tiny")
            self.assertFalse(loaded.auto_record_teams)

