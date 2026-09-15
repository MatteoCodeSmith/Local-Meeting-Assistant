import json
import threading
from unittest.mock import Mock, patch

import pytest

from local_meeting_assistant.config import AppConfig
from local_meeting_assistant.domain import SessionSource
from local_meeting_assistant.quality import recap_sources, SourceLine
from local_meeting_assistant.storage import SessionRepository
from local_meeting_assistant.summarizer import LMStudioSummarizer
from local_meeting_assistant.topic_recap import analyze_topics
from local_meeting_assistant.transcription import LocalProcessingPipeline


def test_noisy_majority_does_not_block_readable_content():
    text = "\n".join(f"**00:{i:02d} — Io:** [Parlato non affidabile: rumore]" for i in range(9))
    text += "\n**01:00 — Io:** Il report va consegnato lunedì, non venerdì."
    rows, warnings, blocked = recap_sources(text)
    assert not blocked and len(rows) == 1 and warnings


def test_recording_interrupts_recap_but_stop_does_not_reset_interruption():
    pipeline = object.__new__(LocalProcessingPipeline)
    pipeline._recording_active = threading.Event()
    pipeline._recap_interrupt = threading.Event()
    pipeline.set_recording(True)
    assert pipeline._recording_active.is_set() and pipeline._recap_interrupt.is_set()
    pipeline.set_recording(False)
    assert not pipeline._recording_active.is_set() and pipeline._recap_interrupt.is_set()


def test_recap_refused_during_any_recording_before_unloading_whisper(tmp_path):
    pipeline = object.__new__(LocalProcessingPipeline)
    pipeline._recording_active = threading.Event()
    pipeline._recording_active.set()
    pipeline._recap_interrupt = threading.Event()
    pipeline._dispose_model = Mock()
    record = SessionRepository(tmp_path).create(SessionSource.MANUAL)
    with pytest.raises(RuntimeError, match="registrazione in corso"):
        pipeline._summarize_session(record, "testo", [])
    pipeline._dispose_model.assert_not_called()


def test_cancel_before_summary_never_loads_model():
    event = threading.Event()
    event.set()
    generator = LMStudioSummarizer("http://localhost:1234/v1", "gemma", 30, 12000, "Italiano",
                                  cancel_event=event)
    with patch.object(LMStudioSummarizer, "_load_model_for_recap") as load:
        with pytest.raises(InterruptedError):
            generator.summarize("testo", "test")
        load.assert_not_called()


def test_summary_failure_restores_whisper(tmp_path):
    pipeline = object.__new__(LocalProcessingPipeline)
    pipeline.config = AppConfig()
    pipeline.repository = SessionRepository(tmp_path)
    record = pipeline.repository.create(SessionSource.MANUAL)
    pipeline._recording_active = threading.Event()
    pipeline._recap_interrupt = threading.Event()
    pipeline.callbacks = Mock()
    pipeline._dispose_model = Mock()
    pipeline._load_model = Mock()
    with patch.object(LMStudioSummarizer, "summarize", side_effect=InterruptedError("Nuovo meeting")):
        pipeline._summarize_session(record, "testo", [])
    pipeline._dispose_model.assert_called_once()
    pipeline._load_model.assert_called_once()
    assert (record.directory / "recap-error.txt").exists()


def test_cleanup_failure_uses_cpu_not_second_gpu_model(tmp_path):
    pipeline = object.__new__(LocalProcessingPipeline)
    pipeline.config = AppConfig()
    pipeline.repository = SessionRepository(tmp_path)
    record = pipeline.repository.create(SessionSource.MANUAL)
    pipeline._recording_active = threading.Event()
    pipeline._recap_interrupt = threading.Event()
    pipeline.callbacks = Mock()
    pipeline._dispose_model = Mock()
    pipeline._load_model = Mock()
    pipeline._load_gguf_model = Mock()
    def summary(generator, *args):
        generator.cleanup_warning = "server non disponibile"
        return "# Recap"
    with patch.object(LMStudioSummarizer, "summarize", summary):
        pipeline._summarize_session(record, "testo", [])
    pipeline._load_model.assert_not_called()
    pipeline._load_gguf_model.assert_called_once_with(force_cpu=True)


def test_cancellation_is_not_swallowed_by_section_recovery():
    chat = Mock(side_effect=InterruptedError("Whisper priority"))
    with pytest.raises(InterruptedError):
        analyze_topics(chat, "gemma", [SourceLine(1, "00:00", "Io", "testo")], 1000, "Italiano")
    assert chat.call_count == 1


def test_bad_merge_preserves_successful_sections():
    def chat(model, *, system, user):
        payload = json.loads(user)
        if "dialogue" not in payload:
            return "not json"
        row = payload["dialogue"][0]
        return json.dumps({"overview": "Tema " + str(row["id"]), "topics": [{
            "title": "Tema " + str(row["id"]), "analysis": "Test", "outcome": "", 
            "actions": [], "open_questions": [], "source_ids": [row["id"]],
        }]})
    warnings = []
    result = analyze_topics(chat, "gemma", [SourceLine(i, "00:00", "Io", "testo " * 100)
                                           for i in range(3)], 800, "Italiano", 6000, warnings)
    assert len(result["topics"]) == 3 and warnings


def test_new_recording_closes_inflight_http_response_without_waiting_for_timeout():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import time
    received, release, cancel = threading.Event(), threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()
            self.wfile.flush()
            received.set()
            release.wait(5)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    server_thread.start()
    generator = LMStudioSummarizer(f"http://127.0.0.1:{server.server_port}", "gemma", 30, 12000,
                                  "Italiano", cancel_event=cancel)
    errors = []
    def request():
        try:
            generator._chat("gemma", system="test", user="test")
        except Exception as exc:
            errors.append(exc)
    worker = threading.Thread(target=request, daemon=True)
    try:
        worker.start()
        assert received.wait(3)
        started = time.monotonic()
        cancel.set()
        worker.join(2)
        assert not worker.is_alive() and time.monotonic() - started < 2
        assert isinstance(errors[0], InterruptedError)
    finally:
        release.set()
        worker.join(2)
        server.shutdown()
        server.server_close()
        server_thread.join(1)
