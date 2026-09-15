import json
from unittest import TestCase

from local_meeting_assistant.summarizer import LMStudioSummarizer, split_text


class SplitTextTests(TestCase):
    def test_preserves_all_text(self) -> None:
        source = "".join(f"linea {index:03d} testo\n" for index in range(200))
        chunks = split_text(source, 500)
        rebuilt = "".join(chunks).replace("\n", "")
        expected = source.replace("\n", "")
        self.assertEqual(rebuilt, expected)
        self.assertGreater(len(chunks), 1)

    def test_rejects_tiny_chunks(self) -> None:
        with self.assertRaises(ValueError):
            split_text("hello", 100)


class FakeSummarizer(LMStudioSummarizer):
    def __init__(self) -> None:
        super().__init__("http://localhost:1234/v1", "", 10, 1000, "Italiano", 16_384)
        self.native_calls: list[tuple[str, str, dict[str, object] | None]] = []

    def _native_request(self, method, endpoint, body=None):
        self.native_calls.append((method, endpoint, body))
        if method == "GET":
            return {
                "models": [
                    {
                        "type": "llm",
                        "key": "whisper-large-v3-turbo",
                        "architecture": "whisper",
                        "size_bytes": 800,
                        "loaded_instances": [],
                    },
                    {
                        "type": "llm",
                        "key": "google/gemma-3-4b",
                        "architecture": "gemma3",
                        "size_bytes": 3000,
                        "loaded_instances": [{"id": "old-gemma"}],
                    },
                    {
                        "type": "llm",
                        "key": "large-chat",
                        "architecture": "llama",
                        "size_bytes": 5000,
                        "loaded_instances": [],
                    },
                ]
            }
        if endpoint == "/models/load":
            return {"model_instance_id": "recap-gemma"}
        return {"instance_id": body["instance_id"]}

    def _chat(self, model: str, *, system: str, user: str) -> str:
        payload = json.loads(user)
        row = payload.get("dialogue", payload.get("original_sources"))[0]
        return json.dumps(
            {
                "overview": "testo del meeting",
                "topics": [
                    {
                        "title": "Argomento",
                        "analysis": "testo del meeting",
                        "source_ids": [row["id"]],
                        "outcome": "Provvisorio",
                        "open_questions": [],
                        "actions": [],
                    }
                ],
            }
        )


class ModelLifecycleTests(TestCase):
    def test_uses_smallest_text_model_and_releases_it(self) -> None:
        summarizer = FakeSummarizer()
        result = summarizer.summarize("testo del meeting", "Riunione")

        self.assertIn("testo del meeting", result)
        self.assertIn(
            ("POST", "/models/unload", {"instance_id": "old-gemma"}), summarizer.native_calls
        )
        load = next(call for call in summarizer.native_calls if call[1] == "/models/load")
        self.assertEqual(load[2]["model"], "google/gemma-3-4b")
        self.assertEqual(load[2]["context_length"], 16_384)
        self.assertFalse(load[2]["offload_kv_cache_to_gpu"])
        self.assertEqual(
            summarizer.native_calls[-1],
            ("POST", "/models/unload", {"instance_id": "recap-gemma"}),
        )
